import json
from types import SimpleNamespace

import pytest

from avk import backup


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    (tmp_path / "secrets" / "platform").mkdir(parents=True)
    return tmp_path


def write_env(home, extra=""):
    (home / "secrets" / "platform" / "backup.env").write_text(f"RESTIC_PASSWORD=pw-SECRET-123\n{extra}", encoding="utf-8")


R2 = "R2_ACCOUNT_ID=acc1\nR2_BUCKET=bkt\nR2_ACCESS_KEY_ID=AKIDSECRET\nR2_SECRET_ACCESS_KEY=SKSECRET\n"


def test_settings_local_only_by_default(home):
    write_env(home)
    s = backup.load_settings()
    assert s.offsite is None and s.local_repo == backup.DEFAULT_LOCAL_REPO
    assert [r.name for r in backup.repos(s)] == ["local"]


def test_settings_with_r2_builds_an_offsite_repo(home):
    write_env(home, R2)
    s = backup.load_settings()
    assert s.offsite.url == "s3:https://acc1.r2.cloudflarestorage.com/bkt/avk"
    assert s.offsite.env["AWS_ACCESS_KEY_ID"] == "AKIDSECRET" and s.offsite.env["AWS_DEFAULT_REGION"] == "auto"


def test_placeholder_r2_values_do_not_count(home):
    write_env(home, R2.replace("AKIDSECRET", "replace-me"))
    assert backup.load_settings().offsite is None


def test_missing_password_is_an_error(home):
    (home / "secrets" / "platform" / "backup.env").write_text("X=1\n")
    with pytest.raises(backup.BackupError, match="RESTIC_PASSWORD"):
        backup.load_settings()


class FakeRestic:
    def __init__(self, fail_on=None, drive_missing=False):
        self.calls, self.fail_on, self.drive_missing = [], fail_on, drive_missing

    def ensure_repo(self, repo):
        if self.drive_missing and repo.name == "local":
            raise backup.BackupError("backup drive Z: is not connected")

    def call(self, repo, args, timeout=0):
        self.calls.append((repo.name, args))
        bad = self.fail_on and self.fail_on[0] == repo.name and self.fail_on[1] == args[0]
        return SimpleNamespace(returncode=1 if bad else 0, stderr="boom", stdout="")


def fake_stage(monkeypatch, tmp_files=("db/hello.dump",)):
    def stage(stage_dir, runner=None):
        for f in tmp_files:
            p = stage_dir / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("PLAINTEXT-SECRET")
        return ["hello"]

    monkeypatch.setattr(backup, "stage", stage)


def test_successful_run_wipes_staging_and_writes_heartbeat(home, monkeypatch, capsys):
    write_env(home)
    fake_stage(monkeypatch)
    fr = FakeRestic()
    assert backup.run_backup(backup.load_settings(), fr) == 0
    assert not (home / "backups" / "stage").exists()                      # plaintext staging is gone
    hb = json.loads((home / "state" / "backup-last.json").read_text())
    assert hb["ok"] is True and hb["databases"] == ["hello"] and hb["repos"] == {"local": "ok"}
    assert "off-site (R2) is not configured" in capsys.readouterr().out


def test_retention_is_7_daily_4_weekly_6_monthly_with_stable_grouping(home, monkeypatch):
    write_env(home)
    fake_stage(monkeypatch)
    fr = FakeRestic()
    backup.run_backup(backup.load_settings(), fr)
    forget = next(a for _, a in fr.calls if a[0] == "forget")
    joined = " ".join(forget)
    assert "--keep-daily 7" in joined and "--keep-weekly 4" in joined and "--keep-monthly 6" in joined
    assert "--group-by host,tags" in joined and "--prune" in forget


def test_failed_backup_alerts_returns_nonzero_and_still_wipes(home, monkeypatch, capsys):
    write_env(home)
    fake_stage(monkeypatch)
    assert backup.run_backup(backup.load_settings(), FakeRestic(fail_on=("local", "backup"))) == 1
    assert not (home / "backups" / "stage").exists()
    assert "BACKUP FAILED" in capsys.readouterr().out
    assert json.loads((home / "state" / "backup-last.json").read_text())["ok"] is False
    assert "BACKUP FAILED" in (home / "logs" / "alerts.log").read_text()


def test_unplugged_drive_fails_before_any_dump_is_taken(home, monkeypatch):
    write_env(home)
    called = []
    monkeypatch.setattr(backup, "stage", lambda *a, **k: called.append(1) or [])
    assert backup.run_backup(backup.load_settings(), FakeRestic(drive_missing=True)) == 1
    assert called == []
    assert "not connected" in (home / "logs" / "alerts.log").read_text()


def test_an_offsite_failure_fails_the_run_even_if_local_worked(home, monkeypatch):
    write_env(home, R2)
    fake_stage(monkeypatch)
    assert backup.run_backup(backup.load_settings(), FakeRestic(fail_on=("offsite", "backup"))) == 1


def test_leftover_staging_from_a_crashed_run_is_wiped_first(home, monkeypatch):
    write_env(home)
    old = home / "backups" / "stage"
    old.mkdir(parents=True)
    (old / "stale.txt").write_text("left over")
    fake_stage(monkeypatch)
    backup.run_backup(backup.load_settings(), FakeRestic())
    assert not old.exists()


def test_restic_secrets_travel_in_the_environment_not_the_command_line(home):
    write_env(home, R2)
    s = backup.load_settings()
    seen = []

    def runner(argv, env=None, **kw):
        seen.append((argv, env))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    r = backup.Restic(s, runner)
    r.call(s.offsite, ["snapshots"])
    argv, env = seen[0]
    flat = " ".join(argv)
    assert "pw-SECRET-123" not in flat and "AKIDSECRET" not in flat and "SKSECRET" not in flat
    assert env["RESTIC_PASSWORD"] == "pw-SECRET-123" and env["AWS_SECRET_ACCESS_KEY"] == "SKSECRET"
    assert "s3:https://acc1" in env["RESTIC_REPOSITORY"]


def test_compare_flags_missing_tables_and_extra_rows_but_tolerates_newer_prod_rows():
    problems, notes = backup.compare({"public.a": 5, "public.b": 2}, {"public.a": 5})
    assert any("public.b" in p and "missing" in p for p in problems)
    problems, notes = backup.compare({"public.a": 5}, {"public.a": 3})
    assert problems == [] and "2 newer row" in notes[0]
    problems, _ = backup.compare({"public.a": 5}, {"public.a": 9})
    assert problems


def test_row_counts_parses_psql_output():
    class P:
        returncode, stderr = 0, ""
        stdout = "public.greeting|1\npublic.flyway_schema_history|1\n"

    assert backup.row_counts(lambda db, sql: P(), "hello") == {"public.greeting": 1, "public.flyway_schema_history": 1}


def test_restore_to_prod_requires_the_typed_confirmation(home, monkeypatch, capsys):
    write_env(home)
    monkeypatch.setattr(backup, "Restic", lambda s: FakeRestic())

    def fake_restore_snapshot(restic, repo, target):
        d = target / "db"
        d.mkdir(parents=True)
        (d / "hello.dump").write_text("x")
        return d

    monkeypatch.setattr(backup, "restore_snapshot", fake_restore_snapshot)
    dumped = []
    monkeypatch.setattr(backup, "dump_database", lambda *a, **k: dumped.append(a))
    assert backup.restore("hello", "local", "prod", None) == 1
    assert 'restore hello to prod' in capsys.readouterr().out and dumped == []     # nothing touched without the phrase
    assert backup.restore("hello", "local", "prod", "yes please") == 1
    assert dumped == []


def test_restore_from_unconfigured_offsite_is_refused(home, capsys):
    write_env(home)
    assert backup.restore("hello", "offsite", "scratch", None) == 1
    assert "not configured" in capsys.readouterr().out


def test_restoring_the_login_database_reapplies_its_single_owner_role_not_app_roles(home, monkeypatch):
    write_env(home)
    monkeypatch.setattr(backup, "Restic", lambda s: FakeRestic())

    def fake_restore_snapshot(restic, repo, target):
        d = target / "db"
        d.mkdir(parents=True)
        (d / "keycloak.dump").write_text("x")
        return d

    monkeypatch.setattr(backup, "restore_snapshot", fake_restore_snapshot)
    monkeypatch.setattr(backup, "dump_database", lambda *a, **k: None)
    monkeypatch.setattr(backup, "sh", lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})())
    from avk import dbadmin

    calls = []
    monkeypatch.setattr(dbadmin, "create_keycloak", lambda env: calls.append(("keycloak", env)) or 0)
    monkeypatch.setattr(dbadmin, "create", lambda app, env: calls.append(("app", app)) or 0)
    assert backup.restore("keycloak", "local", "prod", "restore keycloak to prod") == 0
    assert calls == [("keycloak", "prod")]
