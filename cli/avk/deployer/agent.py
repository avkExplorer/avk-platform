"""`avk deploy-agent --once`, `avk deploy <app> [--digest]`, `avk rollback <app>`.

The agent polls GitHub (outbound only), takes the newest `prod` Deployment with no final status, and hands it to the
blue/green deployer, which proves the trust chain before touching anything. The Deployment is only a trigger.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import yaml

from avk import config
from avk.deployer import dockerops, github, trust
from avk.deployer.bluegreen import Deployer
from avk.deployer.state import AppState, Lock, Log, alert
from avk.deployer.traefik import VolumeRoutes


def host_http(url: str, headers: dict | None = None) -> tuple[int, str]:
    """GET from the PC itself (local Traefik with a Host header, or the public URL). (0, '') when unreachable."""
    req = urllib.request.Request(url, headers={"User-Agent": "avk-deployer", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, ""


def build(gh: github.GitHub | None, platform=None) -> Deployer:
    platform = platform or config.load()
    docker = dockerops.Docker()

    def contract(app: str, sha: str) -> dict:
        if gh is None:
            raise RuntimeError("no GitHub token configured")
        repo = next(a["repo"] for a in platform.raw["apps"] if str(a.get("name")) == app)
        return yaml.safe_load(gh.file(repo, "avk.yaml", sha or "main"))

    return Deployer(platform.raw, platform.github_owner, docker, VolumeRoutes(), AppState, trust.check, contract,
                    gh=gh, log=Log(), alert=alert, host_http=host_http)


def trigger_from(app: str, dep: dict) -> trust.Trigger | None:
    payload = dep.get("payload") or {}
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    if payload.get("app") != app:
        return None
    return trust.Trigger(app=app, image=str(payload.get("image", "")), digest=str(payload.get("digest", "")),
                         sha=dep.get("sha"), deployment_id=dep["id"])


def poll_app(d: Deployer, gh: github.GitHub, app: dict) -> str:
    name, repo = str(app["name"]), app.get("repo")
    if not repo or app.get("status", "stg") == "retired":
        return f"{name}: skipped"
    d.retire_old(name)
    state = AppState(name)
    dep = github.pending(gh.deployments(repo), lambda i: gh.statuses(repo, i), state.load().get("handled", []))
    if dep is None:
        return f"{name}: nothing to deploy"
    t = trigger_from(name, dep)
    if t is None:
        d.log("trigger_rejected", app=name, deployment=dep["id"], reason="payload names a different app")
        gh.set_status(repo, dep["id"], "failure", "trust:registered: payload does not name this app")
        d.alert(f"deployment {dep['id']} in {repo} does not name app {name}")
        state.mark_handled(dep["id"])
        return f"{name}: rejected deployment {dep['id']}"
    outcome = d.deploy(t)
    state.mark_handled(dep["id"])
    return f"{name}: {'ok' if outcome.ok else 'FAILED at ' + outcome.step}"


def run_once(only: str | None = None, quiet_without_token: bool = False) -> int:
    platform = config.load()
    if quiet_without_token and not github.token_available():
        print("deployer: no GitHub token configured yet (tools\\set-github-token.ps1); nothing to do")
        return 0
    gh = github.GitHub(github.load_token())
    d = build(gh, platform)
    with Lock() as lock:
        if not lock.held:
            print("another deployer run is in progress")
            return 0
        failed = 0
        for app in platform.apps:
            if only and str(app.get("name")) != only:
                continue
            try:
                line = poll_app(d, gh, app)
            except Exception as exc:  # one app's problem must not stop the others
                line = f"{app.get('name')}: error {type(exc).__name__}: {str(exc)[:160]}"
                d.log("agent_error", app=str(app.get("name")), error=str(exc)[:200])
                failed += 1
            print(line)
        return 1 if failed else 0


def cmd_agent(args) -> int:
    return run_once(quiet_without_token=True)   # the scheduled task must not fail every 5 minutes before setup


def cmd_deploy(args) -> int:
    platform = config.load()
    app = next((a for a in platform.apps if str(a.get("name")) == args.app), None)
    if app is None:
        print(f"fail  '{args.app}' is not in platform.yaml")
        return 1
    if args.force and not args.digest:
        # redeploy whatever is live now (for example after rotating a secret)
        current = AppState(args.app).load().get("current")
        if not current:
            print(f"fail  {args.app} is not deployed yet")
            return 1
        args.digest, args.sha = current["digest"], current.get("sha")
    if not args.digest:
        return run_once(only=args.app)
    gh = github.GitHub(github.load_token())
    d = build(gh, platform)
    with Lock() as lock:
        if not lock.held:
            print("another deployer run is in progress")
            return 1
        t = trust.Trigger(app=args.app, image=trust.expected_image(platform.github_owner, args.app), digest=args.digest,
                          sha=getattr(args, "sha", None), deployment_id=None, source="manual")
        out = d.deploy(t, force=args.force)
        print("ok    deployed" if out.ok else f"REFUSED at {out.step}: {out.reason}")
        return 0 if out.ok else 1


def cmd_rollback(args) -> int:
    platform = config.load()
    state = AppState(args.app).load()
    prev = state.get("previous")
    if not prev:
        print(f"fail  {args.app} has no previous digest in state")
        return 1
    gh = github.GitHub(github.load_token())
    d = build(gh, platform)
    with Lock() as lock:
        if not lock.held:
            print("another deployer run is in progress")
            return 1
        t = trust.Trigger(app=args.app, image=prev["image"], digest=prev["digest"], sha=prev.get("sha"),
                          deployment_id=None, source="rollback")
        out = d.deploy(t, rollback=True)
        print(f"ok    rolled back to {prev['digest'][:19]}" if out.ok else f"REFUSED at {out.step}: {out.reason}")
        return 0 if out.ok else 1
