"""`avk prod up|down|status`: the prod Docker Compose stack on this PC (tunnel, Traefik, Postgres; app routes come from the deployer)."""

from __future__ import annotations

import json
import os
import subprocess

from avk import config

COMPOSE_FILE = config.REPO_ROOT / "runtime" / "compose.prod.yaml"
PROJECT = "avk-prod"


def slash(path) -> str:
    return str(path).replace("\\", "/")


def compose_env() -> dict[str, str]:
    return {**os.environ, "AVK_HOME_SLASH": slash(config.avk_home())}


def compose(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
                          env=compose_env(), text=True, capture_output=capture, encoding="utf-8", errors="replace")


def live_apps() -> dict[str, str]:
    """App name -> live colour, from the deployer's state files."""
    from avk.deployer.state import AppState

    out = {}
    for a in config.load().apps:
        color = AppState(str(a.get("name"))).load().get("live_color")
        if color:
            out[str(a.get("name"))] = color
    return out


def start_live_colours() -> None:
    """After a reboot Docker restarts containers itself (restart: unless-stopped); this covers any that were stopped."""
    for app, color in live_apps().items():
        subprocess.run(["docker", "start", f"{app}-{color}"], capture_output=True, text=True)


def prepare() -> str | None:
    """Write the Traefik route file; return an error message if a prerequisite is missing."""
    token = config.avk_home() / "secrets" / "platform" / "tunnel.token"
    if not token.is_file():
        return f"{token} missing: run `avk tf apply` first"
    from avk import secretgen

    secretgen.ensure_platform(("prod",))  # the database password file the compose file mounts
    from avk import auth
    from avk.deployer.traefik import VolumeRoutes

    from avk import truststore

    problem = truststore.ensure_host_roots(config.avk_home() / "certs")   # Keycloak's compose file mounts this file
    if problem:
        return problem
    if auth.prepare_stack("prod") != 0:                      # Keycloak image and secret files
        return "could not prepare Keycloak (see above)"
    routes = VolumeRoutes()
    routes.ensure_volume()
    # App routes are written by the deployer. The Phase 1 placeholder route is removed if an older run left it.
    routes.restore("phase1-test", None)
    try:
        routes.write("keycloak", auth.route_yaml(auth.auth_host()))   # the shared login's public route
    except RuntimeError as exc:
        return str(exc)
    return None


def status_rows(ps_json: str) -> list[tuple[str, str]]:
    rows = []
    for line in ps_json.splitlines():
        line = line.strip()
        if line.startswith("{"):
            c = json.loads(line)
            rows.append((c.get("Service", "?"), c.get("Health") or c.get("State", "?")))
    return rows


def main(args) -> int:
    action = args.action
    if action == "up":
        if (err := prepare()):
            print(f"fail  {err}")
            return 1
        from avk import dbadmin

        if compose(["up", "-d", "--wait", "--wait-timeout", "120", "pg-prod"]).returncode != 0:
            return 1
        if dbadmin.create_keycloak("prod") != 0:              # its database must exist before Keycloak starts
            return 1
        code = compose(["up", "-d", "--wait", "--wait-timeout", "180"]).returncode
        if code != 0:
            compose(["ps"])
        else:
            start_live_colours()
        return code
    if action == "down":
        return compose(["down"]).returncode
    out = compose(["ps", "--all", "--format", "json"], capture=True)
    if out.returncode != 0:
        print(out.stderr.strip() or "docker compose failed")
        return 1
    rows = status_rows(out.stdout)
    if not rows:
        print("prod stack is not running")
        return 1
    for name, state in rows:
        print(f"{'ok  ' if state in ('healthy', 'running') else 'fail'}  {name:<12} {state}")
    return 0 if all(s in ("healthy", "running") for _, s in rows) else 1
