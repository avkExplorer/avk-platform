"""`avk stg up|down|status [app] [--build | --digest REF]` and `avk seed <app>`: the stg stack on 127.0.0.1.

Stg holds synthetic data only. Prod data never enters it and its network (avk-stg) shares nothing with prod (avk-prod).
App containers get the same hardening as prod (read-only, no capabilities, limits, no Docker socket, no host folders)
and publish only 127.0.0.1:<stg_port> -> the app's container port.
"""

from __future__ import annotations

import html
import os
import subprocess
import time
from pathlib import Path

import yaml

from avk import config, dbadmin, secretgen
from avk.prod import slash

COMPOSE_FILE = config.REPO_ROOT / "runtime" / "compose.stg.yaml"
PROJECT = "avk-stg"
NETWORK = "avk-stg"


def sh(argv: list[str], capture: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=stdin, text=True, capture_output=capture, encoding="utf-8", errors="replace")


def compose(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "AVK_HOME_SLASH": slash(config.avk_home())}
    return subprocess.run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args], env=env, text=True,
                          capture_output=capture, encoding="utf-8", errors="replace")


def app_dir(app: str) -> Path:
    """Apps live next to the platform repo in the workspace: <workspace>\\<app>."""
    return config.REPO_ROOT.parent / app


def container_name(app: str) -> str:
    return f"{app}-stg"


def local_tag(app: str) -> str:
    return f"avk-stg/{app}:local"


def index_html(apps: list[dict]) -> str:
    rows = []
    for a in apps:
        name = html.escape(str(a.get("name")))
        port = int(a["stg_port"])
        rows.append(f'<li><a href="http://127.0.0.1:{port}/">{name}</a> <span>port {port}</span></li>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AVK stg</title>
<style>:root{{--bg:#f6f7f9;--fg:#14181f;--muted:#5a6472}}@media(prefers-color-scheme:dark){{:root{{--bg:#0f1218;--fg:#e8ebf0;--muted:#98a2b3}}}}
body{{margin:0;padding:32px 16px;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif}}
main{{max-width:34rem;margin:auto}}li{{margin:.3rem 0}}span{{color:var(--muted);font-size:.85em}}</style></head>
<body><main><h1>AVK stg</h1><p>Test environment on this PC. Nothing here is public.</p>
<ul>{''.join(rows) or '<li>No apps registered.</li>'}</ul>
<p><a href="http://127.0.0.1:4811/">Mailpit (caught email)</a></p></main></body></html>
"""


def write_index(apps: list[dict]) -> None:
    d = config.avk_home() / "state" / "stg" / "index"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(index_html(apps), encoding="utf-8")


def contract(app: str) -> dict:
    path = app_dir(app) / "avk.yaml"
    if not path.is_file():
        raise SystemExit(f"fail  {path} not found")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_args(app: str, port: int, container_port: int, env_file: Path, image: str) -> list[str]:
    return ["docker", "run", "-d", "--name", container_name(app), "--network", NETWORK, "--restart", "unless-stopped",
            "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--memory", "512m", "--cpus", "1", "--pids-limit", "256",
            "--env-file", str(env_file), "--label", f"avk.app={app}", "--label", "avk.env=stg",
            "-p", f"127.0.0.1:{port}:{container_port}", image]


def ensure_infra(apps: list[dict]) -> int:
    from avk import auth, dbadmin

    secretgen.ensure_platform(("stg",))
    write_index(apps)
    if auth.prepare_stack("stg") != 0:                    # Keycloak image and secret files
        return 1
    if compose(["up", "-d", "--wait", "--wait-timeout", "120", "pg-stg"]).returncode != 0:
        return 1
    if dbadmin.create_keycloak("stg") != 0:               # its database must exist before Keycloak starts
        return 1
    code = compose(["up", "-d", "--wait", "--wait-timeout", "180"]).returncode
    if code != 0:
        compose(["ps"])
    return code


def prepare_image(app: str, build: bool, digest: str | None) -> str:
    cfg = contract(app)
    if digest:
        owner = config.load().github_owner.lower()
        ref = digest if digest.startswith("ghcr.io/") else f"ghcr.io/{owner}/{app}@{digest}"
        pulled = sh(["docker", "pull", "-q", ref])
        if pulled.returncode != 0:
            raise SystemExit(f"fail  pull {ref}: {pulled.stderr.strip()[-200:]}")
        return ref
    have = sh(["docker", "image", "inspect", local_tag(app)]).returncode == 0
    if build or not have:
        build_cmd = cfg.get("build")
        if build_cmd:
            print(f"building {app}: {' '.join(build_cmd)}")
            if subprocess.run(build_cmd, cwd=app_dir(app)).returncode != 0:
                raise SystemExit(f"fail  build command for {app} failed")
        done = subprocess.run(["docker", "build", "-q", "-t", local_tag(app), str(app_dir(app))])
        if done.returncode != 0:
            raise SystemExit(f"fail  docker build for {app} failed")
    return local_tag(app)


def up_app(app: dict, build: bool, digest: str | None) -> int:
    name, port = str(app["name"]), int(app["stg_port"])
    cfg = contract(name)
    files = secretgen.app_files(name, "stg")
    if not files["runtime"].is_file():
        secretgen.init_app(name, "stg")
    if dbadmin.create(name, "stg") != 0:
        return 1
    image = prepare_image(name, build, digest)
    sh(["docker", "rm", "-f", container_name(name)])
    migration = (cfg.get("migrations") or {}).get("command")
    if migration:
        mig = sh(["docker", "run", "--rm", "--network", NETWORK, "--env-file", str(files["migrator"]), "--read-only",
                  "--tmpfs", "/tmp:rw,nosuid,size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                  "--entrypoint", migration[0], image, *migration[1:]])
        if mig.returncode != 0:
            print(f"fail  migration for {name}: {(mig.stdout + mig.stderr).strip()[-300:]}")
            return 1
    started = sh(run_args(name, port, int(cfg["port"]), files["runtime"], image))
    if started.returncode != 0:
        print(f"fail  start {name}: {started.stderr.strip()[-300:]}")
        return 1
    print(f"ok    {name} starting at http://127.0.0.1:{port}/")
    return 0


def apps_selected(only: str | None) -> list[dict]:
    apps = [a for a in config.load().apps if a.get("status", "stg") != "retired"]
    if only:
        apps = [a for a in apps if str(a.get("name")) == only]
        if not apps:
            raise SystemExit(f"fail  '{only}' is not in platform.yaml")
    return apps


def status() -> int:
    out = compose(["ps", "--all", "--format", "json"], capture=True)
    from avk.prod import status_rows

    rows = status_rows(out.stdout) if out.returncode == 0 else []
    apps = sh(["docker", "ps", "-a", "--filter", "label=avk.env=stg", "--format", "{{.Names}}\t{{.Status}}"]).stdout
    for line in apps.splitlines():
        n, _, s = line.partition("\t")
        rows.append((n, "healthy" if "healthy" in s else ("running" if s.startswith("Up") else s)))
    if not rows:
        print("stg stack is not running")
        return 1
    for n, s in rows:
        print(f"{'ok  ' if s in ('healthy', 'running') else 'fail'}  {n:<14} {s}")
    return 0 if all(s in ("healthy", "running") for _, s in rows) else 1


def main(args) -> int:
    action = args.action
    if action == "status":
        return status()
    if action == "down":
        if args.app:
            sh(["docker", "rm", "-f", container_name(args.app)])
            print(f"stopped {container_name(args.app)}")
            return 0
        names = sh(["docker", "ps", "-aq", "--filter", "label=avk.env=stg"]).stdout.split()
        if names:
            sh(["docker", "rm", "-f", *names])
        return compose(["down"]).returncode
    apps = apps_selected(args.app)
    if (code := ensure_infra([a for a in config.load().apps if a.get("status", "stg") != "retired"])) != 0:
        return code
    worst = 0
    for a in apps:
        worst = max(worst, up_app(a, args.build, args.digest))
    return worst


# ------------------------------------------------------------------------------------------------------ avk seed
def seed(args) -> int:
    """Load synthetic data into STG only. The hook contract is in docs/test-data-hook.md."""
    if args.env != "stg":
        print("fail  `avk seed` only ever writes to stg. Prod data is never created from synthetic files.")
        return 1
    folder = app_dir(args.app) / "seed"
    files = sorted(folder.glob("*.sql")) if folder.is_dir() else []
    if not files:
        print(f"fail  no seed/*.sql files in {folder}")
        return 1
    secretgen.ensure_platform(("stg",))
    for f in files:
        done = dbadmin.psql("stg", args.app, f.read_text(encoding="utf-8"))
        if done.returncode != 0:
            print(f"fail  {f.name}: {done.stderr.strip().splitlines()[-1] if done.stderr.strip() else 'error'}")
            return 1
        print(f"ok    loaded {f.name}")
    return 0
