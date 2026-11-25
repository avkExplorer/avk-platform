"""`avk db create <app> --env`: create an app's database and its three least-privilege roles inside pg-stg / pg-prod.

  <app>_migrator  owns the database and its schema (used only by the one-shot migration container)
  <app>_rw        data access only: SELECT, INSERT, UPDATE, DELETE and sequence use. Cannot change the schema.
  <app>_ro        SELECT only
Idempotent: safe to run again; it also re-applies the passwords from the secret files, so rotating a secret and
re-running `db create` brings the database in line. SQL goes to psql on stdin, so no password is ever on a command line.
"""

from __future__ import annotations

import subprocess

from avk import config, secretgen


def container_name(env: str) -> str:
    return f"avk-{env}-pg-{env}-1"


def build_sql(app: str, passwords: dict[str, str]) -> tuple[str, str]:
    """Return (server-level SQL, database-level SQL). `app` was validated by config.name_errors, passwords are hex."""
    r = secretgen.db_roles(app)
    roles = "\n".join(
        f"""  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{r[k]}') THEN
    CREATE ROLE {r[k]} LOGIN PASSWORD '{passwords[k]}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE {r[k]} LOGIN PASSWORD '{passwords[k]}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;"""
        for k in ("migrator", "rw", "ro"))
    server = f"""DO $$
BEGIN
{roles}
END
$$;
SELECT 'CREATE DATABASE {app} OWNER {r['migrator']}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '{app}')\\gexec
ALTER DATABASE {app} OWNER TO {r['migrator']};
REVOKE ALL ON DATABASE {app} FROM PUBLIC;
GRANT CONNECT ON DATABASE {app} TO {r['rw']}, {r['ro']}, {r['migrator']};
"""
    database = f"""ALTER SCHEMA public OWNER TO {r['migrator']};
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO {r['rw']}, {r['ro']};
ALTER DEFAULT PRIVILEGES FOR ROLE {r['migrator']} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r['rw']};
ALTER DEFAULT PRIVILEGES FOR ROLE {r['migrator']} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {r['rw']};
ALTER DEFAULT PRIVILEGES FOR ROLE {r['migrator']} IN SCHEMA public GRANT SELECT ON TABLES TO {r['ro']};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r['rw']};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r['rw']};
GRANT SELECT ON ALL TABLES IN SCHEMA public TO {r['ro']};
"""
    return server, database


def psql(env: str, database: str, sql: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-i", container_name(env), "psql", "-U", "postgres", "-d", database,
                           "-v", "ON_ERROR_STOP=1", "-q", "-X"],
                          input=sql, text=True, capture_output=True, encoding="utf-8", errors="replace")


def passwords_from_files(app: str, env: str) -> dict[str, str]:
    files = secretgen.app_files(app, env)
    out = {}
    for kind, role in (("runtime", "rw"), ("migrator", "migrator"), ("ro", "ro")):
        if not files[kind].is_file():
            raise SystemExit(f"fail  {files[kind]} missing: run `avk secrets init {app} --env {env}` first")
        out[role] = secretgen.parse_env(files[kind].read_text(encoding="utf-8"))["SPRING_DATASOURCE_PASSWORD"]
    return out


def create(app: str, env: str) -> int:
    if (errors := config.name_errors(app)):
        print(f"fail  app name {app}: {'; '.join(errors)}")
        return 1
    server_sql, db_sql = build_sql(app, passwords_from_files(app, env))
    first = psql(env, "postgres", server_sql)
    if first.returncode != 0:
        print("fail  creating roles/database:", first.stderr.strip().splitlines()[-1] if first.stderr.strip() else "unknown error")
        return 1
    second = psql(env, app, db_sql)
    if second.returncode != 0:
        print("fail  applying privileges:", second.stderr.strip().splitlines()[-1] if second.stderr.strip() else "unknown error")
        return 1
    print(f"ok    database {app} and roles {', '.join(secretgen.db_roles(app).values())} are in place on pg-{env}")
    return 0


def main(args) -> int:
    return create(args.app, args.env)
