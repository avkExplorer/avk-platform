"""Local health checks: the right problems become alerts, once, and a resolved notice follows."""

from avk import health

GB = 1_000_000_000


def container(running=True, status="running", healthy="healthy", restarts=0):
    return {"running": running, "status": status, "health": healthy, "restarts": restarts}


def all_good():
    return {n: container() for n in health.PROD_CONTAINERS} | {"hello-blue": container(healthy="")}


class Rig:
    def __init__(self, tmp_path, containers=None, disk_pct=40, drive=None):
        self.containers, self.disk_pct, self.drive = containers if containers is not None else all_good(), disk_pct, drive or []
        self.sent, self.state = [], tmp_path / "health.json"

    def run(self, **kw):
        return health.run(now=1_000_000.0, inspect=lambda names: self.containers, paths={"System (C:)": "C:\\"},
                          usage=lambda p: (100 * GB, self.disk_pct * GB, (100 - self.disk_pct) * GB),
                          apps=lambda: ["hello-blue"], drive=lambda: self.drive,
                          send=lambda subject, body, key=None, force=False: self.sent.append((subject, key, force)) or "sent",
                          state_path=self.state, **kw)


def test_a_healthy_prod_raises_nothing(tmp_path):
    r = Rig(tmp_path)
    assert r.run() == [] and r.sent == []


def test_disk_warning_at_80_and_critical_at_90(tmp_path):
    r = Rig(tmp_path, disk_pct=85)
    assert [m for _, m in r.run()] == ["System (C:) disk is 85% full (above 80%)"]
    r.disk_pct = 93
    r.run()
    subjects = [s[0] for s in r.sent]
    assert "PROBLEM: System (C:) disk is 93% full (above 90%)" in subjects            # escalation is a new alert, not a repeat
    assert "RESOLVED: System (C:) disk is 85% full (above 80%)" in subjects          # and the lower warning is closed


def test_a_stopped_container_is_a_problem_but_a_missing_one_means_prod_was_taken_down_on_purpose(tmp_path):
    c = all_good()
    c["avk-prod-keycloak-1"] = container(running=False, status="exited")
    del c["avk-prod-traefik-1"]
    r = Rig(tmp_path, containers=c)
    assert [m for _, m in r.run()] == ["keycloak is not running (exited)"]


def test_pg_prod_unhealthy_has_its_own_subject(tmp_path):
    c = all_good()
    c["avk-prod-pg-prod-1"] = container(healthy="unhealthy")
    r = Rig(tmp_path, containers=c)
    r.run()
    assert r.sent[0][0] == "PROBLEM: pg-prod is unhealthy"


def test_a_restart_loop_is_found_by_comparing_with_the_previous_check(tmp_path):
    r = Rig(tmp_path)
    r.run()                                                                         # first sighting records the counts
    r.containers["hello-blue"]["restarts"] = 4
    problems = r.run()
    assert any("hello-blue keeps restarting" in m for _, m in problems)


def test_each_problem_is_emailed_when_it_appears_and_a_resolved_notice_follows(tmp_path):
    r = Rig(tmp_path, disk_pct=85)
    r.run()
    r.run()                                                                         # still there: no second mail from the checker
    assert len(r.sent) == 1 and r.sent[0][0].startswith("PROBLEM:")
    r.disk_pct = 40
    assert r.run() == []
    assert r.sent[-1][0] == "RESOLVED: System (C:) disk is 85% full (above 80%)" and r.sent[-1][2] is True


def test_when_docker_does_not_answer_container_checks_are_skipped_not_reported(tmp_path):
    c = all_good()
    c["avk-prod-pg-prod-1"] = container(healthy="unhealthy")
    r = Rig(tmp_path, containers=c)
    r.run()
    r.containers = None                                                             # docker down now (docker-watch's job)
    r.sent.clear()
    r.containers = None
    assert health.run(now=1_000_000.0, inspect=lambda names: None, paths={}, usage=None, apps=lambda: [], drive=lambda: [],
                      send=lambda *a, **k: r.sent.append(a) or "sent", state_path=r.state) == []
    assert r.sent == []                                                             # no false "resolved" for a problem we could not re-check


def test_backup_drive_missing_is_reported(tmp_path):
    r = Rig(tmp_path, drive=[("backup-drive-missing", "backup drive D: is not connected, so tonight's backup will fail")])
    r.run()
    assert "backup drive D: is not connected" in r.sent[0][0]
