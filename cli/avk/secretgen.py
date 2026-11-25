"""`avk secrets`: generate credentials for the platform and for each app, outside git, and never print them.

Layout under AVK_HOME\\secrets (owner-only):
  prod\\postgres-admin.password, stg\\postgres-admin.password   Postgres superuser for each stack (platform use only)
  platform\\backup.env                                          restic repository password (the owner keeps a copy elsewhere)
  <env>\\<app>.env            the app's runtime settings; connects as <app>_rw; Flyway disabled
  <env>\\<app>.migrator.env   passed ONLY to the one-shot migration container; connects as <app>_migrator
  <env>\\<app>.ro.env         read-only role for reporting and restores

Existing values are never overwritten unless a key is named with --rotate. Values are hex, so they are safe inside
JDBC URLs, SQL and env files without quoting.
"""

from __future__ import annotations

import os
import secrets as _secrets
from pathlib import Path

from avk import config

ENVS = ("stg", "prod")
CREDENTIAL_MARKERS = ("PASSWORD", "SECRET", "TOKEN", "KEY")


def secrets_dir(env: str) -> Path:
    return config.avk_home() / "secrets" / env


def new_value(nbytes: int = 24) -> str:
    return _secrets.token_hex(nbytes)


def is_credential(key: str) -> bool:
    return any(m in key.upper() for m in CREDENTIAL_MARKERS)


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def write_private(path: Path, text: str) -> None:
    """Atomic write (temp file, then rename) so a crash never leaves a half-written secret."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def pg_host(env: str) -> str:
    return f"pg-{env}"


def db_roles(app: str) -> dict[str, str]:
    return {"rw": f"{app}_rw", "migrator": f"{app}_migrator", "ro": f"{app}_ro"}


def ensure_platform(env_names: tuple[str, ...] = ENVS) -> list[str]:
    """Create the platform's own secrets if missing. Returns what was created (names only)."""
    created = []
    for env in env_names:
        path = secrets_dir(env) / "postgres-admin.password"
        if not path.is_file():
            write_private(path, new_value() + "\n")
            created.append(f"{env}/postgres-admin.password")
    backup = config.avk_home() / "secrets" / "platform" / "backup.env"
    if not backup.is_file():
        write_private(backup, f"RESTIC_PASSWORD={new_value(32)}\n")
        created.append("platform/backup.env (restic password: copy it to your password manager)")
    return created


def app_files(app: str, env: str) -> dict[str, Path]:
    d = secrets_dir(env)
    return {"runtime": d / f"{app}.env", "migrator": d / f"{app}.migrator.env", "ro": d / f"{app}.ro.env"}


def build_app_envs(app: str, env: str, existing: dict[str, dict[str, str]], rotate: set[str]) -> dict[str, dict[str, str]]:
    """Return the three env mappings for an app, keeping existing passwords unless rotated."""
    roles = db_roles(app)
    url = f"jdbc:postgresql://{pg_host(env)}:5432/{app}"

    def password(kind: str) -> str:
        key = "SPRING_DATASOURCE_PASSWORD"
        old = existing.get(kind, {}).get(key)
        if old and key not in rotate and f"{kind}.{key}" not in rotate and kind not in rotate:
            return old
        return new_value()

    return {
        "runtime": {"SPRING_DATASOURCE_URL": url, "SPRING_DATASOURCE_USERNAME": roles["rw"],
                    "SPRING_DATASOURCE_PASSWORD": password("runtime"), "SPRING_FLYWAY_ENABLED": "false"},
        "migrator": {"SPRING_DATASOURCE_URL": url, "SPRING_DATASOURCE_USERNAME": roles["migrator"],
                     "SPRING_DATASOURCE_PASSWORD": password("migrator"), "SPRING_FLYWAY_ENABLED": "true"},
        "ro": {"SPRING_DATASOURCE_URL": url, "SPRING_DATASOURCE_USERNAME": roles["ro"],
               "SPRING_DATASOURCE_PASSWORD": password("ro")},
    }


def init_app(app: str, env: str, rotate: set[str] | None = None) -> list[str]:
    """Write the app's secret files. Returns messages that name files and keys, never values."""
    rotate = rotate or set()
    files = app_files(app, env)
    existing = {k: parse_env(p.read_text(encoding="utf-8")) for k, p in files.items() if p.is_file()}
    built = build_app_envs(app, env, existing, rotate)
    messages = []
    for kind, path in files.items():
        merged = {**existing.get(kind, {}), **built[kind]}
        before = existing.get(kind)
        write_private(path, "".join(f"{k}={v}\n" for k, v in merged.items()))
        if before is None:
            messages.append(f"created {path.relative_to(config.avk_home())}")
        elif before != merged:
            messages.append(f"updated {path.relative_to(config.avk_home())} (rotated: {', '.join(sorted(rotate)) or 'none'})")
        else:
            messages.append(f"kept    {path.relative_to(config.avk_home())}")
    return messages


def main(args) -> int:
    action = args.action
    if action == "platform":
        created = ensure_platform()
        print("\n".join(f"created {c}" for c in created) if created else "platform secrets already exist")
        return 0
    app = args.app
    if app not in {str(a.get("name")) for a in config.load().apps}:
        print(f"fail  '{app}' is not in platform.yaml")
        return 1
    ensure_platform((args.env,))
    for line in init_app(app, args.env, set(args.rotate or [])):
        print(line)
    print("values are not printed; read the files in AVK_HOME\\secrets if you need them")
    return 0
