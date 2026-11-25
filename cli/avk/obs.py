"""`avk obs up|down|status|refresh`: dashboards, metrics and log search for prod, ON DEMAND (Prometheus, Loki, Alloy, Grafana).

Alerts do not depend on this stack. Grafana is reachable only at http://127.0.0.1:4950; Prometheus and Loki have no host port. The
Docker socket is never mounted: instead `up`/`refresh` write the list of containers to scrape and tail (state\\obs\\apps.json and
logs.json) from `docker inspect`, and the stack polls those files every 30 s.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from avk import config, dbadmin, prod, secretgen
from avk.deployer.state import AppState

COMPOSE_FILE = config.REPO_ROOT / "runtime" / "obs" / "compose.obs.yaml"
PROJECT = "avk-obs"
GRAFANA_URL = "http://127.0.0.1:4950"
APP_METRICS_PORT = 8081
MONITOR_ROLE = "avk_monitor"


def state_dir() -> Path:
    return config.avk_home() / "state" / "obs"


# ----------------------------------------------------------------------------------------------------------- secrets
def monitor_sql(password: str) -> str:
    """A read-only monitoring role for postgres-exporter: pg_monitor can read statistics, nothing else, and few connections."""
    return f"""DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{MONITOR_ROLE}') THEN
    CREATE ROLE {MONITOR_ROLE} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 5;
  ELSE
    ALTER ROLE {MONITOR_ROLE} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 5;
  END IF;
END
$$;
GRANT pg_monitor TO {MONITOR_ROLE};
"""


def secret_paths() -> dict[str, Path]:
    base = config.avk_home() / "secrets" / "prod"
    return {"grafana": base / "grafana.env", "monitor": base / "postgres-monitor.env"}


def ensure_secrets(psql: Callable = dbadmin.psql) -> list[str]:
    """Create the Grafana admin password and the Postgres monitoring role once. Values are never printed. Returns what was created."""
    paths, created = secret_paths(), []
    if not paths["grafana"].is_file():
        secretgen.write_private(paths["grafana"], f"GF_SECURITY_ADMIN_PASSWORD={secretgen.new_value(16)}\n")
        created.append("grafana.env")
    if not paths["monitor"].is_file():
        password = secretgen.new_value(16)
        done = psql("prod", "postgres", monitor_sql(password))
        if done.returncode != 0:
            raise SystemExit("fail  creating the monitoring role: " + (done.stderr.strip().splitlines() or ["unknown error"])[-1])
        secretgen.write_private(paths["monitor"],
                                f"DATA_SOURCE_NAME=postgresql://{MONITOR_ROLE}:{password}@pg-prod:5432/postgres?sslmode=disable\n")
        created.append("postgres-monitor.env")
    return created


# ----------------------------------------------------------------------------------------------------------- targets
def inspect_containers(names: list[str]) -> list[dict]:
    """id, name, avk.app label and compose service for each existing container; names that do not exist are skipped."""
    if not names:
        return []
    out = subprocess.run(["docker", "inspect", "--format",
                          '{{.Id}}|{{.Name}}|{{index .Config.Labels "avk.app"}}|{{index .Config.Labels "com.docker.compose.service"}}', *names],
                         capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    rows = []
    for line in out.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 4:
            clean = lambda v: "" if v in ("<no value>", "<nil>") else v           # Docker prints this for a missing label
            rows.append({"id": parts[0], "name": parts[1].lstrip("/"), "app": clean(parts[2]), "service": clean(parts[3])})
    return rows


def prod_container_names(apps: list[str] | None = None) -> list[str]:
    names = ["avk-prod-traefik-1", "avk-prod-cloudflared-1", "avk-prod-pg-prod-1", "avk-prod-keycloak-1"]
    for app in (apps if apps is not None else [str(a.get("name")) for a in config.load().apps]):
        color = AppState(app).load().get("live_color")
        if color:
            names.append(f"{app}-{color}")
    return names


def static_apps() -> frozenset[str]:
    """Apps served by nginx (type static): they log JSON but expose no Prometheus metrics."""
    try:
        return frozenset(str(a.get("name")) for a in config.load().apps if a.get("type") == "static")
    except Exception:  # noqa: BLE001
        return frozenset()


def build_targets(rows: list[dict], static: frozenset[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    """(prometheus app targets, alloy log targets) for these containers. Only containers labelled avk.app expose metrics,
    except static apps (no metrics endpoint)."""
    metrics, logs = [], []
    for row in rows:
        app = row["app"] or row["service"] or row["name"]
        logs.append({"targets": ["localhost"], "labels": {
            "__path__": f"/var/lib/docker/containers/{row['id']}/{row['id']}-json.log", "container": row["name"], "app": app, "env": "prod"}})
        if row["app"] and row["app"] not in static:
            metrics.append({"targets": [f"{row['name']}:{APP_METRICS_PORT}"], "labels": {"app": row["app"], "container": row["name"]}})
    return metrics, logs


def write_targets(rows: list[dict]) -> tuple[int, int]:
    metrics, logs = build_targets(rows, static_apps())
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in (("apps.json", metrics), ("logs.json", logs)):
        tmp = folder / (name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(folder / name)
    return len(metrics), len(logs)


# ----------------------------------------------------------------------------------------------------------- commands
def compose(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args], env=prod.compose_env(),
                          text=True, capture_output=capture, encoding="utf-8", errors="replace")


def up() -> int:
    ps = subprocess.run(["docker", "inspect", "--format", "{{.State.Health.Status}}", "avk-prod-pg-prod-1"],
                        capture_output=True, text=True, timeout=30)
    if ps.returncode != 0 or ps.stdout.strip() != "healthy":
        print("fail  prod is not up (pg-prod is not healthy): run `python -m avk prod up` first")
        return 1
    created = ensure_secrets()
    apps, logs = write_targets(inspect_containers(prod_container_names()))
    done = compose(["up", "-d", "--remove-orphans"])
    if done.returncode != 0:
        return 1
    print(f"ok    observability stack up: {apps} app(s) scraped, {logs} container log(s) tailed")
    print(f"      Grafana {GRAFANA_URL}  user admin, password in {secret_paths()['grafana']} (not shown)")
    if created:
        print(f"      created: {', '.join(created)}")
    print("      stop it with `python -m avk obs down` (data is kept); it is not needed for alerts")
    return 0


def down() -> int:
    return compose(["down"]).returncode


def refresh() -> int:
    apps, logs = write_targets(inspect_containers(prod_container_names()))
    print(f"ok    targets refreshed: {apps} app(s), {logs} container log(s); the stack picks them up within 30 s")
    return 0


def status() -> int:
    ps = compose(["ps", "--format", "table {{.Name}}\t{{.Status}}"])
    stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}"], capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
    mem = [line for line in stats.stdout.splitlines() if line.startswith("avk-obs-")]
    if mem:
        print("memory:")
        for line in mem:
            print("  " + line)
    return ps.returncode


def main(args) -> int:
    return {"up": up, "down": down, "status": status, "refresh": refresh}[args.action]()
