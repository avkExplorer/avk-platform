"""Alert email: goes to the owner only, at most once an hour per subject, never raises, never puts secrets in the message."""

import urllib.error

import pytest

from avk import alerts


T = 1_000_000.0                      # a realistic clock: the limiter compares against "never sent" (0)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    monkeypatch.delenv("AVK_NO_EMAIL", raising=False)          # these tests exercise the real path with a fake sender
    (tmp_path / "secrets" / "platform").mkdir(parents=True)
    (tmp_path / "secrets" / "prod").mkdir(parents=True)
    (tmp_path / "secrets" / "platform" / "owner.tfvars").write_text('forward_to_mailbox = "owner@example.test"\nother = "x@y.z"\n', encoding="utf-8")
    (tmp_path / "secrets" / "prod" / "platform-mail.env").write_text("RESEND_API_KEY=re_secretkey123\n", encoding="utf-8")
    return tmp_path


class Sender:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, env, to, rendered):
        if self.fail:
            raise self.fail
        self.calls.append((to, rendered))


def test_the_owner_address_comes_from_the_private_tfvars_not_the_repo(home):
    assert alerts.owner_address() == "owner@example.test"
    (home / "secrets" / "platform" / "owner.tfvars").write_text("nothing here", encoding="utf-8")
    assert alerts.owner_address() is None


def test_an_alert_goes_only_to_the_owner_with_an_avk_subject(home):
    s = Sender()
    assert alerts.send("BACKUP FAILED: drive D: is not connected", sender=s, now=T) == "sent"
    to, rendered = s.calls[0]
    assert to == "owner@example.test"
    assert rendered["subject"] == "[AVK] BACKUP FAILED: drive D: is not connected"
    assert "re_secretkey123" not in rendered["text"] + rendered["html"]


def test_the_same_subject_is_sent_once_an_hour_even_when_numbers_change(home):
    s = Sender()
    assert alerts.send("deploy of hello sha256:aaaaaaaa1111 refused", sender=s, now=T) == "sent"
    assert alerts.send("deploy of hello sha256:bbbbbbbb2222 refused", sender=s, now=T + 500) == "rate-limited"
    assert alerts.send("disk D: above 90 percent", sender=s, now=T + 600) == "sent"        # a different subject is not held back
    assert alerts.send("deploy of hello sha256:cccccccc3333 refused", sender=s, now=T + 3601) == "sent"
    assert len(s.calls) == 3


def test_force_ignores_the_limit(home):
    s = Sender()
    alerts.send("x", sender=s, now=T)
    assert alerts.send("x", sender=s, now=T + 1, force=True) == "sent"


def test_a_failing_send_never_raises_and_is_logged(home):
    result = alerts.send("x", sender=Sender(fail=urllib.error.URLError("no network")), now=T)
    assert result == "failed"
    assert "alert email failed" in (home / "logs" / "alerts.log").read_text(encoding="utf-8")
    assert alerts.send("x", sender=Sender(), now=T + 1) == "sent"           # and a failed send did not use up the hourly slot


def test_unconfigured_or_disabled_does_nothing(home, monkeypatch):
    (home / "secrets" / "platform" / "owner.tfvars").unlink()
    assert alerts.send("x", sender=Sender(), now=T) == "not-configured"
    monkeypatch.setenv("AVK_NO_EMAIL", "1")
    assert alerts.send("x", sender=Sender(), now=T) == "disabled"


def test_a_missing_mail_key_is_reported_not_raised(home):
    (home / "secrets" / "prod" / "platform-mail.env").unlink()
    assert alerts.send("x", sender=Sender(), now=T) == "failed"


def test_deployer_alert_writes_the_log_and_emails_unless_given_a_test_path(home, monkeypatch, capsys):
    from avk.deployer import state

    sent = []
    monkeypatch.setattr(alerts, "notify", sent.append)
    state.alert("BACKUP FAILED: x", path=home / "t" / "alerts.log")
    assert sent == []                                                     # an explicit path is a test: no email
    state.alert("BACKUP FAILED: y")
    assert sent == ["BACKUP FAILED: y"]
    assert "BACKUP FAILED: y" in (home / "logs" / "alerts.log").read_text(encoding="utf-8")


# --- heartbeats -----------------------------------------------------------------------------------------------------
from avk import heartbeat  # noqa: E402


class Resp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_heartbeat_pings_only_a_configured_https_url(tmp_path):
    f = tmp_path / "heartbeats.env"
    f.write_text("HEARTBEAT_BACKUP_RUN=https://hb.example.test/abc\nHEARTBEAT_DEPLOY_AGENT=http://insecure.test/x\n", encoding="utf-8")
    seen = []
    ok = lambda req, timeout: (seen.append(req.full_url), Resp())[1]
    assert heartbeat.ping("backup-run", opener=ok, path=f) == "pinged" and seen == ["https://hb.example.test/abc"]
    assert heartbeat.ping("deploy-agent", opener=ok, path=f) == "not-configured"          # plain http is refused
    assert heartbeat.ping("backup-verify", opener=ok, path=f) == "not-configured"


def test_a_failing_ping_never_raises(tmp_path):
    f = tmp_path / "heartbeats.env"
    f.write_text("HEARTBEAT_BACKUP_RUN=https://hb.example.test/abc\n", encoding="utf-8")

    def boom(req, timeout):
        raise urllib.error.URLError("down")

    assert heartbeat.ping("backup-run", opener=boom, path=f) == "failed"
    assert heartbeat.ping("backup-run", opener=lambda r, timeout: Resp(500), path=f) == "failed"
    with pytest.raises(ValueError):
        heartbeat.ping("made-up", path=f)


def test_missing_secrets_file_means_not_configured(tmp_path):
    assert heartbeat.ping("backup-run", path=tmp_path / "nope.env") == "not-configured"
