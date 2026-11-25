"""`avk health`: local checks run every 5 minutes by the deploy-agent task; problems are emailed through `avk.alerts`.

Checks: system and data disk above 80% and 90%, the backup drive missing, a prod container stopped, unhealthy (pg-prod gets its own
subject) or restarting repeatedly. A container that does not exist at all means prod was taken down on purpose (`avk prod down`), so it
is not an alert. Each problem is emailed when it first appears (at most once an hour) and a "resolved" mail follows when it clears.
The machine being off cannot be reported from the machine: that is the external uptime monitor's job.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from avk import alerts, config
from avk.deployer.state import AppState

WARN_PCT = 80
CRIT_PCT = 90
RESTART_BURST = 3                 # restarts since the previous check that count as a loop
PROD_CONTAINERS = ("avk-prod-traefik-1", "avk-prod-cloudflared-1", "avk-prod-pg-prod-1", "avk-prod-keycloak-1")

Problem = tuple[str, str]         # (key, message)


def _state_path() -> Path:
    return config.avk_home() / "logs" / "health-state.json"


def docker_inspect(names: list[str]) -> dict[str, dict] | None:
    """name -> {running, status, health, restarts}; names that do not exist are left out; None if Docker does not answer."""
    try:
        out = subprocess.run(["docker", "inspect", "--format",
                              "{{.Name}}|{{.State.Running}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}", *names],
                             capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    found: dict[str, dict] = {}
    for line in out.stdout.splitlines():
        parts = line.strip().lstrip("/").split("|")
        if len(parts) == 5:
            found[parts[0]] = {"running": parts[1] == "true", "status": parts[2], "health": parts[3], "restarts": int(parts[4] or 0)}
    if out.returncode not in (0, 1):                      # 1 = some names missing, which is fine
        return None
    return found


def disk_problems(paths: dict[str, str], usage: Callable[[str], tuple[int, int, int]] = shutil.disk_usage) -> list[Problem]:
    problems = []
    for label, path in paths.items():
        try:
            total, used, _free = usage(path)
        except OSError:
            continue
        pct = round(used * 100 / total) if total else 0
        if pct >= CRIT_PCT:
            problems.append((f"disk:{label}:90", f"{label} disk is {pct}% full (above {CRIT_PCT}%)"))
        elif pct >= WARN_PCT:
            problems.append((f"disk:{label}:80", f"{label} disk is {pct}% full (above {WARN_PCT}%)"))
    return problems


def container_problems(names: list[str], inspect: dict[str, dict], last_restarts: dict[str, int]) -> list[Problem]:
    problems = []
    for name in names:
        info = inspect.get(name)
        if info is None:
            continue                                      # not created: prod was taken down on purpose
        short = name.removeprefix("avk-prod-").removesuffix("-1")
        if not info["running"]:
            problems.append((f"container:{name}:stopped", f"{short} is not running ({info['status']})"))
        elif info["health"] == "unhealthy":
            problems.append((f"container:{name}:unhealthy", f"{short} is unhealthy"))
        if info["restarts"] - last_restarts.get(name, info["restarts"]) >= RESTART_BURST:
            problems.append((f"container:{name}:restarting", f"{short} keeps restarting ({info['restarts'] - last_restarts[name]} times in the last 5 minutes)"))
    return problems


def backup_drive_problem() -> list[Problem]:
    try:
        from avk import backup

        repo = backup.load_settings().local_repo
    except (SystemExit, Exception):                       # backups not configured yet: nothing to check
        return []
    drive = Path(repo).drive
    if drive and not Path(drive + "\\").exists():
        return [("backup-drive-missing", f"backup drive {drive} is not connected, so tonight's backup will fail")]
    return []


def live_app_containers() -> list[str]:
    names = []
    try:
        for app in config.load().apps:
            color = AppState(str(app.get("name"))).load().get("live_color")
            if color:
                names.append(f"{app['name']}-{color}")
    except Exception:  # noqa: BLE001 - a broken platform.yaml is reported by `avk validate`, not here
        pass
    return names


def run(now: float | None = None, inspect: Callable[[list[str]], dict[str, dict] | None] = docker_inspect,
        paths: dict[str, str] | None = None, usage: Callable[[str], tuple[int, int, int]] = shutil.disk_usage,
        apps: Callable[[], list[str]] = live_app_containers, drive: Callable[[], list[Problem]] = backup_drive_problem,
        send: Callable[..., str] = alerts.send, state_path: Path | None = None) -> list[Problem]:
    now = time.time() if now is None else now
    path = state_path or _state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    active: dict[str, str] = state.get("active", {})
    last_restarts: dict[str, int] = state.get("restarts", {})

    paths = paths if paths is not None else {"System (C:)": str(config.avk_home()), "Backup": str(Path(config.avk_home().drive + "\\"))}
    problems = disk_problems(paths, usage) + drive()
    names = list(PROD_CONTAINERS) + apps()
    found = inspect(names)
    restarts = dict(last_restarts)
    if found is not None:                                 # Docker down is docker-watch's job; no container verdict then
        problems += container_problems(names, found, last_restarts)
        restarts = {n: i["restarts"] for n, i in found.items()}

    current = {key: message for key, message in problems}
    for key, message in current.items():
        if key not in active:
            send(f"PROBLEM: {message}", f"{message}.", key=f"health:{key}")
    for key, message in active.items():
        if key not in current and (found is not None or not key.startswith("container:")):
            send(f"RESOLVED: {message}", f"Resolved: {message}.", key=f"health-resolved:{key}", force=True)
    if found is None:                                     # keep container problems we cannot re-check
        current.update({k: m for k, m in active.items() if k.startswith("container:")})

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"active": current, "restarts": restarts}), encoding="utf-8")
    return problems


def main(_args) -> int:
    problems = run()
    for _key, message in problems:
        print(f"problem  {message}")
    print("health: ok" if not problems else f"health: {len(problems)} problem(s) (emailed once an hour at most)")
    return 1 if problems else 0
