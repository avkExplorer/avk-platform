"""Deployer state: per-app JSON, an overlap lock, and the JSON-lines log. Nothing here ever holds a secret."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from avk import config

STALE_LOCK_SECONDS = 3600
COLORS = ("blue", "green")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_dir() -> Path:
    return config.avk_home() / "state"


def other_color(color: str | None) -> str:
    return "green" if color == "blue" else "blue"


class AppState:
    """state\\<app>.json: live colour, current and previous digest, and which old colour to retire."""

    def __init__(self, app: str, root: Path | None = None):
        self.app = app
        self.path = (root or state_dir()) / f"{app}.json"

    def load(self) -> dict:
        if not self.path.is_file():
            return {"app": self.app, "live_color": None, "current": None, "previous": None, "retire": None, "handled": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def record_success(self, color: str, digest: str, image: str, deployment_id: int | None, retire_after: float | None,
                       sha: str | None = None) -> dict:
        data = self.load()
        old = data.get("current")
        if old and old.get("digest") != digest:
            data["previous"] = old
        data["current"] = {"digest": digest, "image": image, "deployed_at": now_iso(), "deployment_id": deployment_id,
                           "sha": sha}
        old_color = data.get("live_color")
        data["live_color"] = color
        data["retire"] = ({"color": old_color, "after": retire_after}
                          if old_color and old_color != color and retire_after is not None else None)
        if deployment_id is not None:
            data["handled"] = (data.get("handled", []) + [deployment_id])[-50:]
        self.save(data)
        return data

    def mark_handled(self, deployment_id: int) -> None:
        data = self.load()
        data["handled"] = (data.get("handled", []) + [deployment_id])[-50:]
        self.save(data)


class Lock:
    """Prevents two deployer runs overlapping. A lock older than an hour is treated as left over from a crash."""

    def __init__(self, path: Path | None = None):
        self.path = path or (state_dir() / "deployer.lock")
        self.held = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.time() - self.path.stat().st_mtime > STALE_LOCK_SECONDS:
                    self.path.unlink(missing_ok=True)
                    continue
                return False
            with os.fdopen(fd, "w") as fh:
                fh.write(f"{os.getpid()} {now_iso()}\n")
            self.held = True
            return True
        return False

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


class Log:
    """One JSON line per step in logs\\deployer.log. Callers pass only names, digests and reasons: never secrets."""

    def __init__(self, path: Path | None = None, echo: bool = True):
        self.path = path or (config.avk_home() / "logs" / "deployer.log")
        self.echo = echo

    def __call__(self, event: str, **fields) -> None:
        line = json.dumps({"ts": now_iso(), "event": event, **fields}, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.echo:
            print(line)


def alert(message: str, path: Path | None = None) -> None:
    """A line in logs\\alerts.log and on screen, and (Phase 7) an email to the owner, at most one per subject per hour.
    A call with an explicit `path` (tests) never sends email."""
    send_email = path is None
    path = path or (config.avk_home() / "logs" / "alerts.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{now_iso()} {message}\n")
    print(f"ALERT {message}")
    if send_email:
        from avk import alerts          # imported here: alerts needs mail, which needs config; keeps this module light

        alerts.notify(message)
