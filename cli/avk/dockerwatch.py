"""`avk docker-watch`: if Docker Desktop is not running (or its engine has hung), start it again so prod comes back.

Why: on 2026-11-25 Docker Desktop stopped by itself while the PC stayed on, and prod (tunnel, Traefik, Keycloak, apps) was down until
someone noticed. The containers have restart policies, so they return by themselves once the engine is back; this only has to bring
the engine back. It is called by the deploy-agent task (every 5 minutes) when `docker info` does not answer. Phase 7 adds the email alert.

Rules that keep it safe:
- A plain "not running" is fixed by starting Docker Desktop, at most once per 10 minutes (state in logs\\dockerwatch.json).
- "Running but the engine does not answer" is left alone for 15 minutes (it may just be starting); after that Docker Desktop is
  stopped and started once, the same recovery the logon task uses for a hung engine.
- Every attempt and result is written to logs\\alerts.log (the same place deploy alerts go).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from avk import config
from avk.deployer import state as deployer_state

COOLDOWN_S = 600          # minimum time between two start attempts
HUNG_AFTER_S = 900        # "running but silent" this long counts as hung
WAIT_S = 240              # how long one run waits for the engine after starting Docker Desktop
DESKTOP = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"

Runner = Callable[[list[str], int], int]


def _run(argv: list[str], timeout: int) -> int:
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 124


def _start_desktop() -> bool:
    if not DESKTOP.is_file():
        return False
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(DESKTOP)], creationflags=flags, close_fds=True)      # survives this command
    return True


def engine_up(run: Runner = _run) -> bool:
    return run(["docker", "info", "--format", "{{.ServerVersion}}"], 25) == 0


def desktop_running(run: Runner = _run) -> bool:
    return run(["cmd", "/c", 'tasklist /FI "IMAGENAME eq Docker Desktop.exe" | find /I "Docker Desktop.exe"'], 15) == 0


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def ensure_engine(run: Runner = _run, start: Callable[[], bool] = _start_desktop, sleep: Callable[[float], None] = time.sleep,
                  now: Callable[[], float] = time.time, state_path: Path | None = None, alert: Callable[[str], None] | None = None,
                  wait_s: int = WAIT_S) -> str:
    """Returns ok | recovered | starting | cooldown | failed | no-desktop."""
    path = state_path or (config.avk_home() / "logs" / "dockerwatch.json")
    say = alert or deployer_state.alert
    data = _load(path)
    if engine_up(run):
        if data.get("down_since"):
            _save(path, {"last_start": data.get("last_start", 0)})
        return "ok"

    t = now()
    down_since = data.get("down_since") or t
    data["down_since"] = down_since
    running = desktop_running(run)

    if running:
        if t - down_since < HUNG_AFTER_S:
            _save(path, data)
            return "starting"                                   # give a starting or busy engine time
        say("docker-watch: Docker Desktop is running but its engine has not answered for 15 minutes; restarting it")
        for image in ("Docker Desktop.exe", "com.docker.backend.exe"):
            run(["taskkill", "/IM", image, "/F"], 30)
        run(["wsl", "--shutdown"], 60)
        sleep(5)
    elif t - float(data.get("last_start", 0)) < COOLDOWN_S:
        _save(path, data)
        return "cooldown"
    else:
        say("docker-watch: Docker Desktop is not running, so prod is down; starting it")

    if not start():
        _save(path, data)
        say(f"docker-watch: cannot start Docker Desktop (not found at {DESKTOP})")
        return "no-desktop"
    data["last_start"] = t
    _save(path, data)
    deadline = now() + wait_s
    while now() < deadline:
        sleep(10)
        if engine_up(run):
            _save(path, {"last_start": t})
            say(f"docker-watch: Docker engine is back after about {int(now() - t)} s; containers restart by themselves")
            return "recovered"
    say(f"docker-watch: Docker Desktop was started but the engine did not answer within {wait_s} s; will not try again for 10 minutes")
    return "failed"


def main(_args) -> int:
    result = ensure_engine()
    print(f"docker-watch: {result}")
    return 0 if result in ("ok", "recovered", "starting", "cooldown") else 1
