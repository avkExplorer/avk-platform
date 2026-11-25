"""`avk logs <app> --env prod|stg [--since 15m] [--request-id ID]`: the app's JSON log lines, optionally for one request.

Reads Docker's own log (rotated, 3 x 10 MB). The request id is the `X-Request-Id` header of the response (Cloudflare's ray id when the
caller sent none), so a line from a browser or `curl -i` leads straight to every log line of that request.
"""

from __future__ import annotations

import re
import subprocess

from avk import config
from avk.deployer.state import AppState

SINCE_RE = re.compile(r"^\d+[smhd]$")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def container_names(app: str, env: str) -> list[str]:
    if env == "stg":
        return [f"{app}-stg"]
    state = AppState(app).load()
    color = state.get("live_color")
    return [f"{app}-{color}"] if color else [f"{app}-blue", f"{app}-green"]


def filter_lines(lines: list[str], request_id: str | None) -> list[str]:
    if not request_id:
        return lines
    needle = f'"request_id":"{request_id}"'
    return [line for line in lines if needle in line]


def main(args) -> int:
    if not SINCE_RE.match(args.since):
        print("fail  --since must look like 30s, 15m, 2h or 1d")
        return 1
    if args.request_id and not ID_RE.match(args.request_id):
        print("fail  --request-id must be 8-64 letters, digits, dots, dashes or underscores")
        return 1
    if args.app not in {str(a.get("name")) for a in config.load().apps}:
        print(f"fail  '{args.app}' is not in platform.yaml")
        return 1
    shown = 0
    for name in container_names(args.app, args.env):
        done = subprocess.run(["docker", "logs", "--since", args.since, name], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        if done.returncode != 0:
            continue                                         # the other colour may not exist
        lines = filter_lines((done.stdout + done.stderr).splitlines(), args.request_id)
        for line in lines[-args.tail:] if args.tail else lines:
            print(line)
            shown += 1
    if shown == 0:
        print(f"no log lines for {args.app} ({args.env}) in the last {args.since}" + (f" with request id {args.request_id}" if args.request_id else ""))
    return 0 if shown else 1
