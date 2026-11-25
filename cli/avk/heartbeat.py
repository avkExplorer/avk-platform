"""Heartbeats ("dead man's switch"): a scheduled job pings an outside service when it succeeds; if the ping stops, the service emails the owner.

This catches what nothing on the PC can: the PC is off, the task scheduler stopped, or a nightly backup silently never ran.
The ping URLs are secrets (anyone with one can fake a heartbeat) and live in secrets\\platform\\heartbeats.env as
`HEARTBEAT_<NAME>=<url>` with NAME in BACKUP_RUN, BACKUP_VERIFY, DEPLOY_AGENT. With no URL for a name this does nothing.
A ping failure never breaks the job it reports on.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from avk import config

NAMES = ("backup-run", "backup-verify", "deploy-agent")
TIMEOUT_S = 10


def url_for(name: str, path: Path | None = None) -> str | None:
    path = path or (config.avk_home() / "secrets" / "platform" / "heartbeats.env")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    wanted = "HEARTBEAT_" + name.upper().replace("-", "_")
    for line in text.splitlines():
        key, _, value = line.strip().partition("=")
        if key == wanted and value.startswith("https://"):
            return value.strip()
    return None


def ping(name: str, opener=urllib.request.urlopen, path: Path | None = None) -> str:
    """Returns pinged | not-configured | failed. Never raises."""
    if name not in NAMES:
        raise ValueError(f"unknown heartbeat {name!r}; known: {', '.join(NAMES)}")
    url = url_for(name, path)
    if not url:
        return "not-configured"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "avk-heartbeat/0.1"})
        with opener(request, timeout=TIMEOUT_S) as resp:
            return "pinged" if resp.status < 300 else "failed"
    except (urllib.error.URLError, OSError, ValueError):
        return "failed"
