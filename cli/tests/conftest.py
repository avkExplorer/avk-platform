"""Tests never touch the real machine: no alert email, and AVK_HOME points at an empty temp folder (a test that needs its own
home sets it again). Without this a test could read real secrets or ping a real heartbeat URL."""

import pytest


@pytest.fixture(autouse=True)
def isolated_machine(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("AVK_NO_EMAIL", "1")
    monkeypatch.setenv("AVK_HOME", str(tmp_path_factory.mktemp("avk_home")))
