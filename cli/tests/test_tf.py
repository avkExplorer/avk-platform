import os
import time
from types import SimpleNamespace

import pytest

from avk import tf

SECRETS = {"CLOUDFLARE_API_TOKEN": "tok-SECRET-123", "CLOUDFLARE_ACCOUNT_ID": "acc", "CLOUDFLARE_ZONE_ID": "zone"}


def test_parse_env_file():
    text = "# comment\nA=1\nB = \"two\"\n\nC='3'\nnot a line\n"
    assert tf.parse_env_file(text) == {"A": "1", "B": "two", "C": "3"}


def test_load_secrets_missing_file(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        tf.load_secrets(tmp_path / "nope.env")


def test_load_secrets_rejects_placeholders(tmp_path):
    p = tmp_path / "cloudflare.env"
    p.write_text("CLOUDFLARE_API_TOKEN=replace-me\nCLOUDFLARE_ACCOUNT_ID=a\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="CLOUDFLARE_API_TOKEN, CLOUDFLARE_ZONE_ID"):
        tf.load_secrets(p)


def test_secrets_never_on_command_line(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    env = tf.container_env(SECRETS)
    argv = tf.docker_argv(["plan"], list(env))
    joined = " ".join(argv)
    assert "tok-SECRET-123" not in joined
    assert "-e CLOUDFLARE_API_TOKEN" in joined
    assert argv[-2:] == [tf.IMAGE, "plan"]


def test_apply_refuses_without_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    monkeypatch.setattr(tf, "load_secrets", lambda: SECRETS)
    monkeypatch.setattr(tf, "terraform", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert tf.main(SimpleNamespace(action="apply", yes=True)) == 1
    assert "no saved plan" in capsys.readouterr().out


def test_apply_refuses_stale_plan_and_needs_yes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    monkeypatch.setattr(tf, "load_secrets", lambda: SECRETS)
    monkeypatch.setattr(tf, "terraform", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    plan = tmp_path / "tfstate" / tf.PLAN_NAME
    plan.parent.mkdir(parents=True)
    plan.write_text("x")
    assert tf.main(SimpleNamespace(action="apply", yes=False)) == 1
    assert "--yes" in capsys.readouterr().out
    old = time.time() - tf.PLAN_MAX_AGE_S - 10
    os.utime(plan, (old, old))
    assert tf.main(SimpleNamespace(action="apply", yes=True)) == 1
    assert "older than an hour" in capsys.readouterr().out
