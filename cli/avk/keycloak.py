"""A small Keycloak admin REST client (standard library only) and the pure helpers `avk auth` needs.

The client logs in with the service-account client `avk-cli` (client credentials), never with a person's password:
a console admin has OTP, and a script cannot type an OTP code. Tests give the client a fake `http` function, so they
never need a running Keycloak.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

Http = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]
MASK = "**********"          # what Keycloak returns in place of a stored SMTP password


def urllib_http(method: str, url: str, headers: dict, data: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        return 0, str(exc.reason).encode()


class KeycloakError(RuntimeError):
    pass


class Keycloak:
    def __init__(self, base_url: str, http: Http | None = None):
        self.base = base_url.rstrip("/")
        self._http = http or urllib_http
        self._token: str | None = None

    # ------------------------------------------------------------------------------------------ plumbing
    def _request(self, method: str, path: str, body=None, form: dict | None = None, auth: bool = True):
        headers = {"Accept": "application/json", "User-Agent": "avk-auth"}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self._token}"
        status, raw = self._http(method, f"{self.base}{path}", headers, data)
        parsed = None
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw.decode("utf-8", "replace")
        return status, parsed

    def _ok(self, method: str, path: str, body=None, expect=(200, 201, 204)):
        status, data = self._request(method, path, body)
        if status not in expect:
            detail = data.get("errorMessage") or data.get("error") if isinstance(data, dict) else str(data)[:120]
            raise KeycloakError(f"{method} {path} -> HTTP {status}: {detail}")
        return data

    def wait_ready(self, seconds: int = 120) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            status, _ = self._request("GET", "/realms/master", auth=False)
            if status == 200:
                return
            time.sleep(3)
        raise KeycloakError(f"Keycloak at {self.base} did not answer within {seconds} s")

    def login(self, username: str, password: str) -> None:
        """Password grant with the built-in `admin-cli` client, as a non-human admin user (no OTP on that user)."""
        status, data = self._request("POST", "/realms/master/protocol/openid-connect/token", auth=False,
                                     form={"grant_type": "password", "client_id": "admin-cli",
                                           "username": username, "password": password})
        if status != 200 or not isinstance(data, dict) or "access_token" not in data:
            raise KeycloakError(f"login as {username} failed (HTTP {status})")
        self._token = data["access_token"]

    # ------------------------------------------------------------------------------------------ realms
    def realm(self, name: str) -> dict | None:
        status, data = self._request("GET", f"/admin/realms/{name}")
        if status == 404:
            return None
        if status != 200:
            raise KeycloakError(f"GET realm {name} -> HTTP {status}")
        return data

    def create_realm(self, rep: dict) -> None:
        self._ok("POST", "/admin/realms", rep)

    def update_realm(self, name: str, rep: dict) -> None:
        self._ok("PUT", f"/admin/realms/{name}", rep)

    # ------------------------------------------------------------------------------------------ generic helpers
    def get(self, path: str):
        status, data = self._request("GET", path)
        if status == 404:
            return None
        if status != 200:
            raise KeycloakError(f"GET {path} -> HTTP {status}")
        return data

    def post(self, path: str, body=None):
        return self._ok("POST", path, body)

    def put(self, path: str, body=None):
        return self._ok("PUT", path, body)

    def delete(self, path: str):
        return self._ok("DELETE", path)


# ---------------------------------------------------------------------------------------------- pure helpers
def drift(desired: dict, actual: dict, ignore: tuple[str, ...] = ()) -> list[str]:
    """Keys of `desired` whose value differs in `actual` (top level; nested dicts compared as a subset)."""
    out = []
    for key, want in desired.items():
        if key in ignore:
            continue
        have = actual.get(key)
        if isinstance(want, dict) and isinstance(have, dict):
            if any(have.get(k) != v for k, v in want.items() if not (key == "smtpServer" and k == "password" and have.get(k) == MASK)):
                out.append(key)
        elif have != want:
            out.append(key)
    return out
