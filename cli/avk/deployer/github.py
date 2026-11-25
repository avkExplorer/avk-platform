"""Minimal GitHub REST client for the deployer: list Deployments, read and set their statuses, read a file at a commit.

The token (fine-grained, selected repositories: Deployments read and write, Contents and Metadata read) comes from
AVK_HOME\\secrets\\platform\\github.env as GITHUB_TOKEN and only ever goes in the Authorization header.
The token can create a Deployment, but it cannot make either signature, so it cannot get anything deployed.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Callable

from avk import config

API = "https://api.github.com"
FINAL_STATES = {"success", "failure", "error", "inactive"}

Http = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]


def urllib_http(method: str, url: str, headers: dict, data: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def token_available() -> bool:
    try:
        load_token()
        return True
    except SystemExit:
        return False


def load_token() -> str:
    path = config.avk_home() / "secrets" / "platform" / "github.env"
    if not path.is_file():
        raise SystemExit(f"fail  {path} not found: run tools\\set-github-token.ps1")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_TOKEN=") and len(line) > 20 and "replace-me" not in line:
            return line.split("=", 1)[1].strip()
    raise SystemExit("fail  GITHUB_TOKEN missing in github.env")


class GitHub:
    def __init__(self, token: str, http: Http | None = None):
        self._token = token
        self._http = http or urllib_http

    def _call(self, method: str, path: str, body: dict | None = None):
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "avk-deployer"}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            headers["Content-Type"] = "application/json"
        status, raw = self._http(method, f"{API}{path}", headers, data)
        return status, (json.loads(raw) if raw else None)

    def deployments(self, repo: str, environment: str = "prod") -> list[dict]:
        status, data = self._call("GET", f"/repos/{repo}/deployments?environment={environment}&per_page=20")
        if status != 200:
            raise RuntimeError(f"GitHub deployments list failed: HTTP {status}")
        return data

    def statuses(self, repo: str, deployment_id: int) -> list[dict]:
        status, data = self._call("GET", f"/repos/{repo}/deployments/{deployment_id}/statuses?per_page=20")
        if status != 200:
            raise RuntimeError(f"GitHub deployment statuses failed: HTTP {status}")
        return data

    def set_status(self, repo: str, deployment_id: int, state: str, description: str) -> None:
        status, _ = self._call("POST", f"/repos/{repo}/deployments/{deployment_id}/statuses",
                               {"state": state, "description": description[:140], "environment": "prod"})
        if status not in (200, 201):
            raise RuntimeError(f"GitHub status update failed: HTTP {status}")

    def file(self, repo: str, path: str, ref: str) -> str:
        status, data = self._call("GET", f"/repos/{repo}/contents/{path}?ref={ref}")
        if status != 200:
            raise RuntimeError(f"GitHub file {path}@{ref[:7]} failed: HTTP {status}")
        return base64.b64decode(data["content"]).decode("utf-8")


def pending(deployments: list[dict], statuses_of: Callable[[int], list[dict]], handled: list[int]) -> dict | None:
    """The newest Deployment with no final status that this PC has not already handled."""
    for d in sorted(deployments, key=lambda x: x["id"], reverse=True):
        if d["id"] in handled:
            continue
        latest = statuses_of(d["id"])
        if latest and latest[0].get("state") in FINAL_STATES:
            continue
        return d
    return None
