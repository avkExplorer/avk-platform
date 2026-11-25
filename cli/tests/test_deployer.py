import base64
import json

import pytest

from avk.deployer import bluegreen, github, trust
from avk.deployer.state import AppState, Lock, Log
from avk.deployer.traefik import Routes, VolumeRoutes, route_yaml

OWNER = "Me"
DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
IMAGE = "ghcr.io/me/hello"
PREDICATE = "https://example.com/approval/v1"
BUILD_ID = "https://github.com/Me/avk-platform/.github/workflows/release.yml@refs/heads/main"
APPROVE_ID = "https://github.com/Me/avk-platform/.github/workflows/approve.yml@refs/heads/main"

PLATFORM = {
    "domain": "example.com",
    "github_owner": OWNER,
    "signing": {
        "issuer": "https://token.actions.githubusercontent.com",
        "build_identity": "https://github.com/Me/avk-platform/.github/workflows/release.yml@{ref}",
        "approval_identity": "https://github.com/Me/avk-platform/.github/workflows/approve.yml@{ref}",
        "approval_predicate_type": PREDICATE,
        "allowed_refs": ["refs/heads/main"],
    },
    "apps": [{"name": "hello", "type": "web", "stg_port": 4820, "repo": "Me/hello", "hosts": ["hello.example.com"]}],
}


def attestation_line(app="hello", digest=DIGEST, subject=None, ptype=PREDICATE):
    statement = {"predicateType": ptype, "subject": [{"digest": {"sha256": (subject or digest).split(":")[1]}}],
                 "predicate": {"app": app, "digest": digest}}
    return json.dumps({"payload": base64.b64encode(json.dumps(statement).encode()).decode()})


class FakeCosign:
    """Answers cosign like a registry that holds one signed, approved digest (configurable)."""

    def __init__(self, build_ok=True, approval_ok=True, approval_out=None, build_identity=BUILD_ID,
                 approval_identity=APPROVE_ID, repo="Me/hello"):
        self.build_ok, self.approval_ok = build_ok, approval_ok
        self.approval_out = approval_out
        self.build_identity, self.approval_identity, self.repo = build_identity, approval_identity, repo
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        flags = dict(zip(argv[3:], argv[4:]))
        if argv[1] == "verify":
            ok = self.build_ok and flags.get("--certificate-identity") == self.build_identity \
                and flags.get("--certificate-github-workflow-repository") == self.repo
            return (0, "", "") if ok else (1, "", "Error: no matching signatures")
        ok = self.approval_ok and flags.get("--certificate-identity") == self.approval_identity \
            and flags.get("--certificate-github-workflow-repository") == self.repo
        if not ok:
            return 1, "", "Error: no matching attestations"
        return 0, (self.approval_out if self.approval_out is not None else attestation_line()) + "\n", ""


def trigger(**kw):
    base = dict(app="hello", image=IMAGE, digest=DIGEST, sha="c" * 40, deployment_id=7)
    base.update(kw)
    return trust.Trigger(**base)


# --------------------------------------------------------------------------------------------------- trust checks
def test_happy_path_trusts():
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign())
    assert v.ok and v.check == "trusted"


def test_unapproved_digest_is_refused():
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign(approval_ok=False))
    assert not v.ok and v.check == "approved"


def test_wrong_build_signer_is_refused():
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign(build_identity="https://github.com/evil/x/.github/workflows/release.yml@refs/heads/main"))
    assert not v.ok and v.check == "signed-build"


def test_signature_from_a_different_repository_is_refused():
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign(repo="someone/else"))
    assert not v.ok and v.check == "signed-build"


def test_approval_for_a_different_digest_is_refused():
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign(approval_out=attestation_line(digest=OTHER)))
    assert not v.ok and v.check == "approved" and "does not match" in v.reason


def test_approval_for_a_different_app_is_refused():
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign(approval_out=attestation_line(app="other")))
    assert not v.ok and v.check == "approved"


def test_approval_whose_subject_differs_from_its_claim_is_refused():
    line = attestation_line(digest=DIGEST, subject=OTHER)
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign(approval_out=line))
    assert not v.ok and v.check == "approved"


def test_approval_with_wrong_predicate_type_is_refused():
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign(approval_out=attestation_line(ptype="https://x/y")))
    assert not v.ok


def test_garbage_attestation_output_fails_closed():
    v = trust.check(PLATFORM, OWNER, trigger(), FakeCosign(approval_out="not json at all"))
    assert not v.ok and v.check == "approved"


def test_cosign_missing_or_timing_out_fails_closed():
    v = trust.check(PLATFORM, OWNER, trigger(), lambda argv: (127, "", "OSError: cosign not found"))
    assert not v.ok


@pytest.mark.parametrize("t,expect", [
    (dict(app="ghost"), "not registered"),
    (dict(image="ghcr.io/evil/hello"), "is not ghcr.io/me/hello"),
    (dict(digest="latest"), "not a sha256 digest"),
    (dict(digest="sha256:123"), "not a sha256 digest"),
])
def test_registration_checks(t, expect):
    v = trust.check(PLATFORM, OWNER, trigger(**t), FakeCosign())
    assert not v.ok and v.check == "registered" and expect in v.reason


def test_retired_app_is_refused():
    p = {**PLATFORM, "apps": [{**PLATFORM["apps"][0], "status": "retired"}]}
    assert not trust.check(p, OWNER, trigger(), FakeCosign()).ok


def test_identities_are_exact_and_use_allowed_refs():
    runner = FakeCosign()
    trust.check(PLATFORM, OWNER, trigger(), runner)
    identities = [a[a.index("--certificate-identity") + 1] for a in runner.calls]
    assert identities == [BUILD_ID, APPROVE_ID]
    assert all("*" not in i and ".*" not in i for i in identities)


def test_same_digest_check():
    assert trust.same_digest(f'["ghcr.io/me/hello@{DIGEST}"]', DIGEST).ok
    assert not trust.same_digest(f'["ghcr.io/me/hello@{OTHER}"]', DIGEST).ok


# ------------------------------------------------------------------------------------------------- blue/green
class FakeDocker:
    def __init__(self, ready=True, migration_code=0, smoke_status=200, smoke_body='{"message":"pong"}', pull_ok=True):
        self.ready, self.migration_code, self.pull_ok = ready, migration_code, pull_ok
        self.smoke_status, self.smoke_body = smoke_status, smoke_body
        self.calls = []

    def pull(self, ref):
        self.calls.append(("pull", ref))
        return self.pull_ok, "" if self.pull_ok else "pull failed"

    def repo_digests(self, ref):
        return json.dumps([ref])

    def run_migration(self, ref, env_file, command):
        self.calls.append(("migrate", ref, env_file.name))
        return self.migration_code, "migration output"

    def start_app(self, name, ref, env_file, labels):
        self.calls.append(("start", name, env_file.name))
        return True, ""

    def stop(self, name):
        self.calls.append(("stop", name))

    def curl(self, url, headers=None):
        self.calls.append(("curl", url))
        if url.endswith("/readyz"):
            return (200 if self.ready else 503), ""
        return self.smoke_status, self.smoke_body


class Env:
    def __init__(self, tmp_path, docker=None, trust_ok=True, host=None, gh=None):
        self.tmp = tmp_path
        self.secrets = tmp_path / "secrets"
        self.secrets.mkdir()
        (self.secrets / "hello.env").write_text("SPRING_DATASOURCE_PASSWORD=runtimepw123\n")
        (self.secrets / "hello.migrator.env").write_text("SPRING_DATASOURCE_PASSWORD=migratorpw456\n")
        self.docker = docker or FakeDocker()
        self.routes = Routes(tmp_path / "traefik")
        self.logfile = tmp_path / "deployer.log"
        self.alerts = []
        self.statuses = []
        self.now = [1000.0]
        self.host_calls = []

        class GH:
            def set_status(_, repo, id_, state, desc):
                self.statuses.append((repo, id_, state, desc))

        verdict = trust.Verdict(True, "", "trusted") if trust_ok else trust.Verdict(False, "no approval", "approved")
        self.trust_calls = []

        def trust_check(p, o, t):
            self.trust_calls.append(t)
            return verdict

        def host_http(url, headers=None):
            self.host_calls.append((url, headers))
            return (host or (lambda u, h: (200, '{"message":"pong"}')))(url, headers)

        self.deployer = bluegreen.Deployer(
            PLATFORM, OWNER, self.docker, self.routes, lambda app: AppState(app, tmp_path / "state"), trust_check,
            lambda app, sha: {"port": 8080, "health": {"ready": "/readyz"},
                              "smoke": [{"path": "/", "expect": 200}, {"path": "/api/v1/ping", "expect": 200, "contains": "pong"}],
                              "migrations": {"command": ["java", "-jar", "/app/app.jar", "--migrate-only"]}},
            gh=gh or GH(), log=Log(self.logfile, echo=False), alert=self.alerts.append, host_http=host_http,
            secrets_root=self.secrets, clock=lambda: self.now[0], sleep=lambda s: self.now.__setitem__(0, self.now[0] + s),
            ready_timeout=10, ready_interval=2, grace=600, public_attempts=2, public_interval=1)

    def kinds(self):
        return [c[0] for c in self.docker.calls]

    def state(self):
        return AppState("hello", self.tmp / "state").load()

    def route(self):
        return self.routes.snapshot("hello")


def test_successful_first_deploy(tmp_path):
    e = Env(tmp_path)
    out = e.deployer.deploy(trigger())
    assert out.ok
    assert e.kinds()[:3] == ["pull", "migrate", "start"]
    assert ("migrate", f"{IMAGE}@{DIGEST}", "hello.migrator.env") in e.docker.calls     # migrator creds, one-shot only
    assert ("start", "hello-blue", "hello.env") in e.docker.calls                          # runtime creds, never migrator
    assert "http://hello-blue:8080" in e.route()
    s = e.state()
    assert s["live_color"] == "blue" and s["current"]["digest"] == DIGEST and s["previous"] is None
    assert e.statuses[0][2] == "in_progress" and e.statuses[-1][2] == "success"


def test_post_switch_smoke_waits_for_traefik_to_reload(tmp_path):
    """Traefik needs ~2 s to load a changed route; the first post-switch request must come after the settle wait."""
    e = Env(tmp_path)
    order = []
    original_point = e.routes.point
    e.routes.point = lambda *a, **k: (order.append("route"), original_point(*a, **k))[1]
    original_sleep = e.deployer.sleep
    e.deployer.sleep = lambda s: (order.append(("sleep", s)), original_sleep(s))[1]
    e.deployer.host_http = lambda url, headers=None: (order.append("host_http"), (200, '{"message":"pong"}'))[1]
    assert e.deployer.deploy(trigger()).ok
    i_route = order.index("route")
    i_first_http = order.index("host_http")
    waited = sum(s for kind, s in (x for x in order[i_route:i_first_http] if isinstance(x, tuple)))
    assert waited >= 4.0 and e.deployer.switch_settle >= 4.0


def test_second_deploy_uses_the_other_colour_and_keeps_previous(tmp_path):
    e = Env(tmp_path)
    assert e.deployer.deploy(trigger()).ok
    assert e.deployer.deploy(trigger(digest=OTHER, deployment_id=8)).ok
    s = e.state()
    assert s["live_color"] == "green" and s["current"]["digest"] == OTHER and s["previous"]["digest"] == DIGEST
    assert "http://hello-green:8080" in e.route()
    assert s["retire"]["color"] == "blue"


def test_old_colour_is_stopped_only_after_the_grace_period(tmp_path):
    e = Env(tmp_path)
    e.deployer.deploy(trigger())
    e.deployer.deploy(trigger(digest=OTHER, deployment_id=8))
    assert e.deployer.retire_old("hello") is False                  # inside the grace period
    assert ("stop", "hello-blue") not in e.docker.calls
    e.now[0] += 601
    assert e.deployer.retire_old("hello") is True
    assert ("stop", "hello-blue") in e.docker.calls and e.state()["retire"] is None


def test_untrusted_release_touches_nothing(tmp_path):
    e = Env(tmp_path, trust_ok=False)
    out = e.deployer.deploy(trigger())
    assert not out.ok and out.step == "trust:approved"
    assert e.docker.calls == [] and e.route() is None
    assert e.statuses[-1][2] == "failure" and "trust:approved" in e.statuses[-1][3]
    assert e.alerts


def test_migration_failure_switches_nothing(tmp_path):
    e = Env(tmp_path, docker=FakeDocker(migration_code=1))
    out = e.deployer.deploy(trigger())
    assert not out.ok and out.step == "migration"
    assert "start" not in e.kinds() and e.route() is None and e.state()["live_color"] is None


def test_readiness_timeout_stops_the_new_colour_and_switches_nothing(tmp_path):
    e = Env(tmp_path, docker=FakeDocker(ready=False))
    out = e.deployer.deploy(trigger())
    assert not out.ok and out.step == "readiness"
    assert ("stop", "hello-blue") in e.docker.calls and e.route() is None


def test_pre_switch_smoke_failure_never_touches_live_traffic(tmp_path):
    good = Env(tmp_path)
    assert good.deployer.deploy(trigger()).ok
    before = good.route()
    good.docker.smoke_status = 500                                   # the new version's ping now returns 500
    out = good.deployer.deploy(trigger(digest=OTHER, deployment_id=8))
    assert not out.ok and out.step == "pre-switch-smoke"
    assert good.route() == before                                    # still pointing at the old colour
    assert ("stop", "hello-green") in good.docker.calls
    assert good.state()["current"]["digest"] == DIGEST
    assert good.statuses[-1][2] == "failure"
    assert not good.host_calls[2:] or all("hello-green" not in str(c) for c in good.host_calls[2:])


def test_post_switch_failure_restores_the_previous_route(tmp_path):
    calls = {"n": 0}

    def host(url, headers):
        calls["n"] += 1
        return (200, '{"message":"pong"}') if calls["n"] <= 4 else (502, "")   # first deploy passes, second breaks after the switch

    e = Env(tmp_path, host=host)
    assert e.deployer.deploy(trigger()).ok
    before = e.route()
    out = e.deployer.deploy(trigger(digest=OTHER, deployment_id=8))
    assert not out.ok and out.step == "post-switch-smoke" and "route restored" in out.reason
    assert e.route() == before
    assert ("stop", "hello-green") in e.docker.calls
    assert e.state()["current"]["digest"] == DIGEST and e.state()["live_color"] == "blue"


def test_post_switch_failure_on_first_deploy_removes_the_route(tmp_path):
    e = Env(tmp_path, host=lambda url, headers: (502, ""))
    out = e.deployer.deploy(trigger())
    assert not out.ok and e.route() is None


def test_already_live_digest_is_not_redeployed(tmp_path):
    e = Env(tmp_path)
    e.deployer.deploy(trigger())
    n = len(e.docker.calls)
    out = e.deployer.deploy(trigger(deployment_id=9))
    assert out.ok and out.step == "already-live" and len(e.docker.calls) == n


def test_force_redeploys_the_live_digest_into_the_other_colour(tmp_path):
    e = Env(tmp_path)
    e.deployer.deploy(trigger())
    e.docker.calls.clear()
    out = e.deployer.deploy(trigger(deployment_id=None), force=True)
    assert out.ok and out.step == "success"
    assert ("start", "hello-green", "hello.env") in e.docker.calls         # fresh containers read the rotated secret file
    assert len(e.trust_calls) == 2                                         # trust checks still ran both times
    assert e.state()["live_color"] == "green"


def test_rollback_skips_migrations_and_returns_to_previous(tmp_path):
    e = Env(tmp_path)
    e.deployer.deploy(trigger())
    e.deployer.deploy(trigger(digest=OTHER, deployment_id=8))
    e.docker.calls.clear()
    out = e.deployer.deploy(trigger(deployment_id=None), rollback=True)
    assert out.ok and "migrate" not in e.kinds()
    assert e.state()["current"]["digest"] == DIGEST and e.state()["live_color"] == "blue"


def test_missing_secrets_stop_the_deploy_before_anything_runs(tmp_path):
    e = Env(tmp_path)
    (e.secrets / "hello.migrator.env").unlink()
    out = e.deployer.deploy(trigger())
    assert not out.ok and out.step == "secrets" and e.docker.calls == []


def test_pull_failure_is_reported(tmp_path):
    e = Env(tmp_path, docker=FakeDocker(pull_ok=False))
    out = e.deployer.deploy(trigger())
    assert not out.ok and out.step == "pull"


def test_secrets_never_appear_in_the_log_or_alerts(tmp_path):
    e = Env(tmp_path, docker=FakeDocker(migration_code=1))
    e.deployer.deploy(trigger())
    e.deployer.deploy(trigger(digest=OTHER, deployment_id=8))
    text = e.logfile.read_text() + " ".join(e.alerts) + str(e.statuses)
    assert "runtimepw123" not in text and "migratorpw456" not in text
    assert all(json.loads(line)["event"] for line in e.logfile.read_text().splitlines())


# --------------------------------------------------------------------------------------------- traefik, lock, github
def test_route_yaml_and_atomic_write(tmp_path):
    text = route_yaml("hello", ["a.example.com", "b.example.com"], "http://hello-blue:8080")
    assert "Host(`a.example.com`) || Host(`b.example.com`)" in text and "http://hello-blue:8080" in text
    assert "priority: 1000" in text      # the real app outranks the Phase 1 placeholder for the same host
    r = Routes(tmp_path)
    r.point("hello", ["a.example.com"], "http://hello-green:8080")
    assert [p.name for p in tmp_path.iterdir()] == ["hello.yml"]    # no temp file left behind
    snap = r.snapshot("hello")
    r.point("hello", ["a.example.com"], "http://hello-blue:8080")
    r.restore("hello", snap)
    assert "hello-green" in r.snapshot("hello")
    r.restore("hello", None)
    assert r.snapshot("hello") is None


def test_lock_prevents_overlap_and_recovers_from_stale(tmp_path):
    a = Lock(tmp_path / "l.lock")
    assert a.acquire()
    b = Lock(tmp_path / "l.lock")
    assert not b.acquire()
    import os, time

    old = time.time() - 7200
    os.utime(tmp_path / "l.lock", (old, old))
    assert b.acquire()                     # a crashed run's lock is taken over
    b.release()
    assert not (tmp_path / "l.lock").exists()


def test_pending_considers_only_the_newest_deployment():
    statuses = {1: [], 2: [{"state": "success"}], 3: []}
    assert github.pending([{"id": 1}, {"id": 2}, {"id": 3}], lambda i: statuses[i], [])["id"] == 3
    assert github.pending([{"id": 1}, {"id": 2}, {"id": 3}], lambda i: statuses[i], [3]) is None   # handled
    done = {1: [], 2: [], 3: [{"state": "failure"}]}
    assert github.pending([{"id": 1}, {"id": 2}, {"id": 3}], lambda i: done[i], []) is None       # final


def test_an_old_unfinished_deployment_is_never_deployed_after_a_newer_one():
    """The hazard: an approved but superseded Deployment with no status must not roll production back."""
    statuses = {1: [], 2: [{"state": "success"}]}
    assert github.pending([{"id": 1}, {"id": 2}], lambda i: statuses[i], []) is None
    assert github.pending([], lambda i: [], []) is None


def test_github_client_sends_token_only_in_the_header():
    seen = []

    def http(method, url, headers, data):
        seen.append((method, url, headers, data))
        return 200, b"[]"

    gh = github.GitHub("ghp_SECRETTOKEN", http)
    gh.deployments("Me/hello")
    assert seen[0][2]["Authorization"] == "Bearer ghp_SECRETTOKEN"
    assert "ghp_SECRETTOKEN" not in seen[0][1]


# --------------------------------------------------------------------------------------- volume-based routes
class FakeVolumeDocker:
    """Records the docker commands VolumeRoutes issues and keeps a fake in-memory volume."""

    def __init__(self):
        self.files, self.calls = {}, []

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        if argv[:3] == ["docker", "volume", "create"]:
            return 0, "", ""
        script = argv[-1]
        if script.startswith("if [ -f"):
            name = script.split("/r/")[1].split(" ")[0]
            return (0, self.files[name], "") if name in self.files else (3, "", "")
        if script.startswith("cat >"):
            name = script.split("&& mv ")[1].split(" ")[1].split("/r/")[1]
            self.files[name] = stdin
            return 0, "", ""
        if script.startswith("rm -f"):
            self.files.pop(script.split("/r/")[1], None)
            return 0, "", ""
        return 1, "", "unexpected"


def test_volume_routes_roundtrip_and_restore():
    fd = FakeVolumeDocker()
    r = VolumeRoutes(runner=fd, image="img@sha256:x")
    assert r.snapshot("hello") is None
    r.point("hello", ["h.example.com"], "http://hello-blue:8080")
    snap = r.snapshot("hello")
    assert "hello-blue" in snap and "priority: 1000" in snap
    r.point("hello", ["h.example.com"], "http://hello-green:8080")
    r.restore("hello", snap)
    assert "hello-blue" in r.snapshot("hello")
    r.restore("hello", None)
    assert r.snapshot("hello") is None


def test_volume_helper_is_locked_down_and_content_goes_on_stdin():
    fd = FakeVolumeDocker()
    r = VolumeRoutes(runner=fd, image="img@sha256:x")
    r.point("hello", ["h.example.com"], "http://hello-blue:8080")
    argv, stdin = next((a, s) for a, s in fd.calls if a[:2] == ["docker", "run"] and s)
    joined = " ".join(argv)
    assert "--network none" in joined and "--read-only" in argv and "--cap-drop ALL" in joined
    assert "no-new-privileges:true" in joined and "docker.sock" not in joined
    assert "avk-prod-routes:/r" in joined and "Host(" not in joined          # the route text is stdin, never argv
    assert "Host(`h.example.com`)" in stdin


@pytest.mark.parametrize("bad", ["", "../x", "a b", "a;rm -rf /", "a$(id)"])
def test_volume_route_names_are_validated(bad):
    r = VolumeRoutes(runner=FakeVolumeDocker(), image="img")
    with pytest.raises(ValueError):
        r.snapshot(bad)
    with pytest.raises(ValueError):
        r.write(bad, "x")
