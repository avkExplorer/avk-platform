"""Blue/green deploy of one approved digest. Steps, in order (specs/phase-4, section C):

  trust checks -> contract -> pull + same-digest -> migration -> start idle colour -> wait ready
  -> PRE-switch smoke on the idle colour (live traffic never touched it)
  -> switch Traefik atomically -> POST-switch smoke (local Traefik with the Host header, and the public URL)
  -> record state, stop the old colour after a grace period.

A failure before the switch leaves production exactly as it was. A failure after it restores the previous route.
Every step is one JSON log line. Dependencies are injected so the whole state machine is unit-tested without Docker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from avk import config
from avk.deployer import trust
from avk.deployer.dockerops import container_name
from avk.deployer.state import AppState, other_color

LOCAL_TRAEFIK = "http://127.0.0.1:4900"


@dataclass
class Outcome:
    ok: bool
    step: str
    reason: str = ""


class Deployer:
    def __init__(self, platform_raw: dict, owner: str, docker, routes, states: Callable[[str], AppState],
                 trust_check: Callable, contract_loader: Callable[[str, str], dict], gh=None, log=None, alert=None,
                 host_http: Callable[[str, dict | None], tuple[int, str]] | None = None, secrets_root: Path | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 ready_timeout: float = 120, ready_interval: float = 3, grace: float = 600,
                 public_attempts: int = 6, public_interval: float = 5):
        self.platform_raw, self.owner, self.docker, self.routes = platform_raw, owner, docker, routes
        self.states, self.trust_check, self.contract_loader, self.gh = states, trust_check, contract_loader, gh
        self.log = log or (lambda event, **f: None)
        self.alert = alert or (lambda msg: None)
        self.host_http = host_http or (lambda url, headers=None: (0, ""))
        self.secrets_root = secrets_root or (config.avk_home() / "secrets" / "prod")
        self.clock, self.sleep = clock, sleep
        self.ready_timeout, self.ready_interval, self.grace = ready_timeout, ready_interval, grace
        self.public_attempts, self.public_interval = public_attempts, public_interval

    # ------------------------------------------------------------------------------------------------- helpers
    def app_cfg(self, app: str) -> dict:
        return next(a for a in self.platform_raw["apps"] if str(a.get("name")) == app)

    def report(self, t: trust.Trigger, state: str, description: str) -> None:
        if self.gh is None or t.deployment_id is None:
            return
        try:
            self.gh.set_status(self.app_cfg(t.app)["repo"], t.deployment_id, state, description)
        except Exception as exc:  # a status that cannot be posted must not hide the real outcome
            self.log("status_post_failed", app=t.app, error=str(exc)[:200])

    def fail(self, t: trust.Trigger, step: str, reason: str) -> Outcome:
        self.log("deploy_failed", app=t.app, digest=t.digest, step=step, reason=reason)
        self.report(t, "failure", f"{step}: {reason}")
        self.alert(f"deploy of {t.app} {t.digest[:19]} refused or failed at {step}: {reason}")
        return Outcome(False, step, reason)

    def smoke(self, fetch: Callable[[str], tuple[int, str]], checks: list[dict]) -> str | None:
        """Run the avk.yaml smoke list through `fetch(path)`. Returns a failure description, or None when all pass."""
        for c in checks:
            status, body = fetch(c["path"])
            if status != int(c.get("expect", 200)):
                return f"GET {c['path']} returned {status}, expected {c.get('expect', 200)}"
            if c.get("contains") and str(c["contains"]) not in body:
                return f"GET {c['path']} did not contain {c['contains']!r}"
        return None

    def wait_ready(self, base: str, ready_path: str) -> bool:
        deadline = self.clock() + self.ready_timeout
        while True:
            status, _ = self.docker.curl(base + ready_path)
            if status == 200:
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(self.ready_interval)

    # -------------------------------------------------------------------------------------------------- deploy
    def deploy(self, t: trust.Trigger, rollback: bool = False) -> Outcome:
        app = t.app
        self.log("deploy_start", app=app, digest=t.digest, deployment=t.deployment_id, rollback=rollback)

        verdict = self.trust_check(self.platform_raw, self.owner, t)
        if not verdict.ok:
            return self.fail(t, f"trust:{verdict.check}", verdict.reason)
        self.log("trust_ok", app=app, digest=t.digest)

        state = self.states(app)
        data = state.load()
        if (data.get("current") or {}).get("digest") == t.digest and data.get("live_color") and not rollback:
            self.log("already_live", app=app, digest=t.digest)
            self.report(t, "success", "already live")
            if t.deployment_id is not None:
                state.mark_handled(t.deployment_id)
            return Outcome(True, "already-live")

        try:
            contract = self.contract_loader(app, t.sha or "")
            port = int(contract["port"])
            ready_path = contract["health"]["ready"]
            smoke = contract.get("smoke") or []
        except Exception as exc:
            return self.fail(t, "contract", f"cannot read avk.yaml at {(t.sha or '?')[:7]}: {str(exc)[:150]}")

        env_file = self.secrets_root / f"{app}.env"
        migrator_file = self.secrets_root / f"{app}.migrator.env"
        if not env_file.is_file() or (not rollback and not migrator_file.is_file()):
            return self.fail(t, "secrets", f"missing secret files for {app}: run `avk secrets init {app} --env prod`")

        ref = f"{t.image}@{t.digest}"
        ok, msg = self.docker.pull(ref)
        if not ok:
            return self.fail(t, "pull", msg or "docker pull failed")
        same = trust.same_digest(self.docker.repo_digests(ref), t.digest)
        if not same.ok:
            return self.fail(t, "trust:same-digest", same.reason)
        self.report(t, "in_progress", "verified, deploying")

        if not rollback:
            command = (contract.get("migrations") or {}).get("command")
            if command:
                code, tail = self.docker.run_migration(ref, migrator_file, command)
                self.log("migration", app=app, exit=code)
                if code != 0:
                    return self.fail(t, "migration", f"exit {code}: {tail[-160:]}")

        live = data.get("live_color")
        idle = other_color(live)
        idle_name = container_name(app, idle)
        labels = {"avk.app": app, "avk.color": idle, "avk.digest": t.digest}
        started, msg = self.docker.start_app(idle_name, ref, env_file, labels)
        if not started:
            return self.fail(t, "start", msg or "could not start the new colour")
        backend = f"http://{idle_name}:{port}"

        if not self.wait_ready(backend, ready_path):
            self.docker.stop(idle_name)
            return self.fail(t, "readiness", f"{ready_path} did not return 200 within {int(self.ready_timeout)}s")
        problem = self.smoke(lambda path: self.docker.curl(backend + path), smoke)
        if problem:
            self.docker.stop(idle_name)  # live traffic never reached this colour
            return self.fail(t, "pre-switch-smoke", problem)
        self.log("pre_switch_smoke_ok", app=app, color=idle)

        hosts = list(self.app_cfg(app).get("hosts") or [])
        snapshot = self.routes.snapshot(app)
        self.routes.point(app, hosts, backend)
        self.log("switched", app=app, color=idle, backend=backend)

        problem = self.post_switch(hosts, smoke)
        if problem:
            self.routes.restore(app, snapshot)
            self.docker.stop(idle_name)
            self.log("rolled_back", app=app, reason=problem)
            return self.fail(t, "post-switch-smoke", problem + " (route restored)")

        retire_after = self.clock() + self.grace
        state.record_success(idle, t.digest, t.image, t.deployment_id, retire_after, t.sha)
        self.log("deploy_success", app=app, digest=t.digest, color=idle, previous=(live or "none"))
        self.report(t, "success", f"live on {idle}")
        return Outcome(True, "success")

    def post_switch(self, hosts: list[str], smoke: list[dict]) -> str | None:
        """Local Traefik with the Host header first, then the public URL (retried: the edge can lag by a few seconds)."""
        host = hosts[0] if hosts else ""
        problem = self.smoke(lambda path: self.host_http(LOCAL_TRAEFIK + path, {"Host": host}), smoke)
        if problem:
            return "via Traefik: " + problem
        for attempt in range(self.public_attempts):
            problem = self.smoke(lambda path: self.host_http(f"https://{host}{path}", None), smoke)
            if problem is None:
                return None
            if attempt + 1 < self.public_attempts:
                self.sleep(self.public_interval)
        return "public URL: " + (problem or "")

    # --------------------------------------------------------------------------------------------- housekeeping
    def retire_old(self, app: str) -> bool:
        """Stop the previous colour once its grace period is over. Its image stays on disk for a manual rollback."""
        state = self.states(app)
        data = state.load()
        retire = data.get("retire")
        if not retire or self.clock() < retire["after"]:
            return False
        self.docker.stop(container_name(app, retire["color"]))
        data["retire"] = None
        state.save(data)
        self.log("old_color_stopped", app=app, color=retire["color"])
        return True
