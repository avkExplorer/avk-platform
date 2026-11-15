"""Locations, platform.yaml loading and the naming/port rules shared by every command."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_FILE = REPO_ROOT / "platform.yaml"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,29}$")
RESERVED = frozenset({"www", "api", "auth", "mail", "status", "admin", "stg", "notify"})
APP_TYPES = frozenset({"web", "api", "ecommerce", "service"})
APP_STATUSES = frozenset({"stg", "live", "retired"})
STG_PORTS = range(4820, 4900)
# Fixed platform ports (also listed in README.md). App stg ports come from STG_PORTS.
PLATFORM_PORTS = {
    4800: "stg index page",
    4810: "Keycloak (stg)",
    4811: "Mailpit web UI",
    4812: "Mailpit SMTP",
    4900: "prod Traefik (local)",
    4910: "Keycloak (prod) admin",
    4950: "Grafana",
    5440: "pg-stg",
}


def avk_home() -> Path:
    """AVK_HOME holds secrets, state and backups outside every repo. Default C:\\avk."""
    return Path(os.environ.get("AVK_HOME") or r"C:\avk")


def name_errors(name: str) -> list[str]:
    """Why an app name is not allowed (empty list when it is fine)."""
    errors = []
    if not NAME_RE.match(name):
        errors.append("must be 2-30 chars: lowercase letters, digits and hyphens, starting with a letter")
    if name.endswith("-") or "--" in name:
        errors.append("must not end with a hyphen or contain '--'")
    for prefix, why in (("api-", "an app's API host"), ("lab-", "an app's AWS lab host")):
        if name.startswith(prefix):
            errors.append(f"must not start with '{prefix}' (reserved for {why})")
    if name in RESERVED:
        errors.append(f"'{name}' is reserved")
    return errors


def next_free_port(used: set[int]) -> int:
    for port in STG_PORTS:
        if port not in used:
            return port
    raise ValueError(f"no free stg port left in {STG_PORTS.start}-{STG_PORTS.stop - 1}")


@dataclass
class Platform:
    domain: str
    github_owner: str
    apps: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def load(path: Path = PLATFORM_FILE) -> Platform:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a mapping")
    return Platform(
        domain=str(data.get("domain", "")),
        github_owner=str(data.get("github_owner", "")),
        apps=list(data.get("apps") or []),
        raw=data,
    )


def validate(p: Platform) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a loaded platform.yaml."""
    errors: list[str] = []
    warnings: list[str] = []
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", p.domain):
        errors.append(f"domain '{p.domain}' is not a valid lowercase domain")
    if not p.github_owner or p.github_owner.startswith("<"):
        warnings.append("github_owner is not set yet (needed from Phase 3)")

    seen_names: set[str] = set()
    seen_ports: set[int] = set()
    for i, app in enumerate(p.apps):
        if not isinstance(app, dict):
            errors.append(f"apps[{i}] must be a mapping")
            continue
        name = str(app.get("name", ""))
        where = f"app '{name or i}'"
        errors += [f"{where}: name {e}" for e in name_errors(name)]
        if name in seen_names:
            errors.append(f"{where}: duplicate name")
        seen_names.add(name)

        if app.get("type") not in APP_TYPES:
            errors.append(f"{where}: type must be one of {sorted(APP_TYPES)}")
        if app.get("status", "stg") not in APP_STATUSES:
            errors.append(f"{where}: status must be one of {sorted(APP_STATUSES)}")

        port = app.get("stg_port")
        if not isinstance(port, int) or port not in STG_PORTS:
            errors.append(f"{where}: stg_port must be an integer in {STG_PORTS.start}-{STG_PORTS.stop - 1}")
        elif port in seen_ports:
            errors.append(f"{where}: stg_port {port} is already used")
        else:
            seen_ports.add(port)

        for host in app.get("hosts") or []:
            suffix = "." + p.domain
            label = host[: -len(suffix)] if host.endswith(suffix) else None
            if label is None:
                errors.append(f"{where}: host '{host}' is not under {p.domain}")
            elif "." in label:
                errors.append(f"{where}: host '{host}' is two levels deep; only <app>.{p.domain} is covered by free TLS")
            elif label not in (name, f"api-{name}"):
                errors.append(f"{where}: host '{host}' must be {name}.{p.domain} or api-{name}.{p.domain}")
    return errors, warnings
