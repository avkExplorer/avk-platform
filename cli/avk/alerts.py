"""`avk alert`: email the owner through the Phase 2 transactional provider (Resend), with a repeat limit.

Every alert is already written to logs\\alerts.log (deployer.state.alert); this module adds the email. It never raises: a failing
alert must not break a deploy or a backup, and a failed send is written to alerts.log instead. The owner's address is read from
secrets\\platform\\owner.tfvars (`forward_to_mailbox`), never from the repo. The same subject is sent at most once per hour.
"""

from __future__ import annotations

import html
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from avk import config, mail

RATE_S = 3600
SEND_TIMEOUT_S = 20
TFVARS_KEY = re.compile(r'^\s*forward_to_mailbox\s*=\s*"([^"]+@[^"]+)"\s*$', re.M)
Sender = Callable[[dict, str, dict], None]


def owner_address(path: Path | None = None) -> str | None:
    path = path or (config.avk_home() / "secrets" / "platform" / "owner.tfvars")
    try:
        found = TFVARS_KEY.search(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return found.group(1) if found else None


def default_key(subject: str) -> str:
    """The same alert with a different digest, id or number counts as the same subject."""
    return re.sub(r"[0-9a-f]{7,}|\d+", "#", subject.lower()).strip()[:80]


def _state_path() -> Path:
    return config.avk_home() / "logs" / "alert-state.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def due(key: str, now: float, path: Path | None = None, rate_s: int = RATE_S) -> bool:
    return now - float(_load(path or _state_path()).get(key, 0)) >= rate_s


def mark(key: str, now: float, path: Path | None = None, rate_s: int = RATE_S) -> None:
    path = path or _state_path()
    data = {k: v for k, v in _load(path).items() if now - float(v) < rate_s}          # forget old entries
    data[key] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _post(env: dict, to: str, rendered: dict) -> None:
    with urllib.request.urlopen(mail.build_send_request(env, to, rendered), timeout=SEND_TIMEOUT_S) as resp:
        if resp.status >= 300:
            raise OSError(f"HTTP {resp.status}")


def _log_failure(text: str) -> None:
    path = config.avk_home() / "logs" / "alerts.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {text}\n")


def send(subject: str, body: str = "", key: str | None = None, force: bool = False, now: float | None = None,
         sender: Sender = _post, state_path: Path | None = None) -> str:
    """Returns sent | rate-limited | disabled | not-configured | failed. Never raises."""
    if os.environ.get("AVK_NO_EMAIL") == "1":
        return "disabled"
    now = time.time() if now is None else now
    key = key or default_key(subject)
    try:
        to = owner_address()
        if not to:
            return "not-configured"
        if not force and not due(key, now, state_path):
            return "rate-limited"
        env = mail.load_mail_env()
        text = f"{body or subject}\n\nHost: {socket.gethostname()}\nLog: {config.avk_home() / 'logs' / 'alerts.log'}\n"
        rendered = {"subject": f"[AVK] {subject}"[:150], "text": text,
                    "html": f"<p style=\"font:16px/1.5 sans-serif\">{html.escape(body or subject)}</p>"
                            f"<p style=\"color:#5a6472;font:13px sans-serif\">Host: {html.escape(socket.gethostname())}</p>"}
        sender(env, to, rendered)
        mark(key, now, state_path)
        return "sent"
    except (urllib.error.URLError, OSError, SystemExit, ValueError) as exc:
        _log_failure(f"alert email failed ({type(exc).__name__}): {str(exc)[:160]}")
        return "failed"


def notify(message: str) -> None:
    """Called by deployer.state.alert for every alert line: the message is both the subject (shortened) and the body."""
    send(message[:110], message)


def main(args) -> int:
    result = send(args.subject, args.body or "", key=args.key, force=args.force)
    print(f"alert: {result}")
    return 0 if result in ("sent", "rate-limited") else 1
