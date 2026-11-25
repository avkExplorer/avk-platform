"""`avk backup run|verify|restore`: encrypted restic backups to the external drive and (when configured) Cloudflare R2.

What is backed up (one restic snapshot per run): a `pg_dump -Fc` of every prod database, the database roles
(`pg_dumpall --globals-only`), and copies of AVK_HOME\\secrets, tfstate and state. Everything in a restic repository is
encrypted with RESTIC_PASSWORD; the owner keeps a copy of that password elsewhere (without it nothing can be restored).

Safety rules: the staging folder holds plaintext secrets and is ALWAYS wiped, even on failure; secrets reach restic only
through environment variables, never on a command line; prod data is restored only into a throwaway container on its own
network (`avk-restore`), never into stg; restoring INTO prod needs a typed confirmation and takes a fresh dump first.
Recovery point: nightly dumps, so up to 24 hours of data can be lost (the owner's choice for Phase 4).
"""

from __future__ import annotations

import json
import os
import secrets as _secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from avk import config, secretgen
from avk.deployer.state import alert, now_iso

DEFAULT_LOCAL_REPO = r"D:\development_backup\avk\restic"
PROD_PG = "avk-prod-pg-prod-1"
RESTORE_NETWORK = "avk-restore"
PG_IMAGE = "postgres:17-alpine@sha256:f9cf3e1f0066f0eb0c48707d864f0ac5941e9ff454a072077d97a4d278d07d73"
KEEP = ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6"]
LIST_DBS_SQL = "select datname from pg_database where not datistemplate and datname <> 'postgres' order by 1"


@dataclass
class Repo:
    name: str                 # "local" or "offsite"
    url: str
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Settings:
    password: str
    local_repo: str
    offsite: Repo | None = None


class BackupError(RuntimeError):
    pass


def backup_env_path() -> Path:
    return config.avk_home() / "secrets" / "platform" / "backup.env"


def load_settings() -> Settings:
    path = backup_env_path()
    if not path.is_file():
        raise BackupError(f"{path} not found: run `avk secrets platform`")
    v = secretgen.parse_env(path.read_text(encoding="utf-8"))
    if not v.get("RESTIC_PASSWORD"):
        raise BackupError("RESTIC_PASSWORD is missing in backup.env")
    offsite = None
    needed = ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    if all(v.get(k) and "replace-me" not in v[k] for k in needed):
        offsite = Repo("offsite", f"s3:https://{v['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com/{v['R2_BUCKET']}/avk",
                       {"AWS_ACCESS_KEY_ID": v["R2_ACCESS_KEY_ID"], "AWS_SECRET_ACCESS_KEY": v["R2_SECRET_ACCESS_KEY"],
                        "AWS_DEFAULT_REGION": "auto"})
    return Settings(v["RESTIC_PASSWORD"], v.get("BACKUP_LOCAL_REPO") or DEFAULT_LOCAL_REPO, offsite)


def repos(s: Settings) -> list[Repo]:
    return [Repo("local", s.local_repo), *([s.offsite] if s.offsite else [])]


def run(argv: list[str], env: dict[str, str] | None = None, stdout=None, stdin: str | None = None, timeout: int = 3600):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(argv, env=full_env, stdout=stdout if stdout else subprocess.PIPE,
                          stderr=subprocess.PIPE, input=None if stdout else stdin, text=False if stdout else True,
                          encoding=None if stdout else "utf-8", errors=None if stdout else "replace", timeout=timeout)


class Restic:
    def __init__(self, settings: Settings, runner=run):
        self.settings, self.runner = settings, runner

    def _env(self, repo: Repo) -> dict[str, str]:
        return {"RESTIC_PASSWORD": self.settings.password, "RESTIC_REPOSITORY": repo.url, **repo.env}

    def call(self, repo: Repo, args: list[str], timeout: int = 3600):
        return self.runner(["restic", *args], env=self._env(repo), timeout=timeout)

    def ensure_repo(self, repo: Repo) -> None:
        if repo.name == "local":
            drive = Path(repo.url).drive
            if drive and not Path(drive + "\\").exists():
                raise BackupError(f"backup drive {drive} is not connected")
        if self.call(repo, ["cat", "config"]).returncode == 0:
            return
        if (made := self.call(repo, ["init"])).returncode != 0:
            raise BackupError(f"cannot create the {repo.name} repository: {_last(made.stderr)}")


def _last(text) -> str:
    text = text if isinstance(text, str) else (text or b"").decode("utf-8", "replace")
    return (text.strip().splitlines() or ["unknown error"])[-1][:200]


# ------------------------------------------------------------------------------------------------------ staging
def prod_databases(runner=run) -> list[str]:
    done = runner(["docker", "exec", PROD_PG, "psql", "-U", "postgres", "-tAc", LIST_DBS_SQL])
    if done.returncode != 0:
        raise BackupError(f"cannot list prod databases (is pg-prod running?): {_last(done.stderr)}")
    return [d for d in done.stdout.split() if d]


def dump_database(db: str, dest: Path, runner=run) -> None:
    with dest.open("wb") as fh:
        done = runner(["docker", "exec", PROD_PG, "pg_dump", "-U", "postgres", "-Fc", db], stdout=fh)
    if done.returncode != 0 or dest.stat().st_size == 0:
        raise BackupError(f"pg_dump of {db} failed: {_last(done.stderr)}")


def dump_globals(dest: Path, runner=run) -> None:
    with dest.open("wb") as fh:
        done = runner(["docker", "exec", PROD_PG, "pg_dumpall", "-U", "postgres", "--globals-only"], stdout=fh)
    if done.returncode != 0 or dest.stat().st_size == 0:
        raise BackupError(f"pg_dumpall --globals-only failed: {_last(done.stderr)}")


def stage(stage_dir: Path, runner=run) -> list[str]:
    """Fill the staging folder. Returns the database names dumped."""
    (stage_dir / "db").mkdir(parents=True, exist_ok=True)
    dbs = prod_databases(runner)
    for db in dbs:
        dump_database(db, stage_dir / "db" / f"{db}.dump", runner)
    dump_globals(stage_dir / "db" / "globals.sql", runner)
    home = config.avk_home()
    # Only what cannot be recreated. Terraform's provider folders hold Linux symlinks and 200+ MB of binaries that
    # `terraform init` downloads again; plan files are single-use and sensitive.
    skip = shutil.ignore_patterns("*.lock", "*.tmp", ".terraform", "plugin-cache", "*.tfplan")
    for name in ("secrets", "tfstate", "state", "private"):
        src = home / name
        if src.is_dir():
            shutil.copytree(src, stage_dir / "files" / name, ignore=skip)
    return dbs


def wipe(path: Path) -> None:
    """The staging folder holds plaintext secrets: always remove it, clearing read-only attributes first."""
    if not path.exists():
        return

    def onerror(func, p, _):
        os.chmod(p, 0o700)
        func(p)

    shutil.rmtree(path, onerror=onerror)


def heartbeat(data: dict) -> None:
    p = config.avk_home() / "state" / "backup-last.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------- run
def run_backup(settings: Settings | None = None, restic: Restic | None = None, runner=run) -> int:
    started = time.time()
    result = {"ts": now_iso(), "ok": False, "databases": [], "repos": {}}
    # A FIXED path: restic groups snapshots by path for retention, so a changing folder name would make every
    # snapshot its own group and nothing would ever be pruned. Leftovers from a crashed run are wiped first.
    stage_dir = config.avk_home() / "backups" / "stage"
    wipe(stage_dir)
    try:
        settings = settings or load_settings()
        restic = restic or Restic(settings)
        targets = repos(settings)
        if settings.offsite is None:
            print("warn  off-site (R2) is not configured yet: only the local copy is made")
        for repo in targets:
            restic.ensure_repo(repo)          # fails early with "drive not connected"
        result["databases"] = stage(stage_dir, runner)
        failures = []
        for repo in targets:
            made = restic.call(repo, ["backup", str(stage_dir), "--tag", "avk", "--host", "avk-pc", "--json", "--quiet"])
            if made.returncode != 0:
                failures.append(f"{repo.name}: {_last(made.stderr)}")
                result["repos"][repo.name] = "failed"
                continue
            kept = restic.call(repo, ["forget", *KEEP, "--prune", "--tag", "avk", "--host", "avk-pc", "--group-by", "host,tags"])
            result["repos"][repo.name] = "ok" if kept.returncode == 0 else "backup ok, retention failed"
            if kept.returncode != 0:
                failures.append(f"{repo.name} retention: {_last(kept.stderr)}")
        if failures:
            raise BackupError("; ".join(failures))
        result["ok"] = True
        result["seconds"] = round(time.time() - started, 1)
        print(f"ok    backed up {len(result['databases'])} database(s) + secrets/tfstate/state to "
              f"{', '.join(r.name for r in targets)} in {result['seconds']}s")
        return 0
    except (BackupError, subprocess.TimeoutExpired, OSError) as exc:
        result["error"] = str(exc)[:300]
        alert(f"BACKUP FAILED: {exc}")
        return 1
    finally:
        wipe(stage_dir)
        heartbeat(result)


# -------------------------------------------------------------------------------------------------------- verify
def sh(argv: list[str], stdin: str | None = None, timeout: int = 600):
    return run(argv, stdin=stdin, timeout=timeout)


class Scratch:
    """A throwaway Postgres on its own internal network, no host port, removed with its volume afterwards."""

    def __init__(self, tag: str | None = None):
        self.tag = tag or _secrets.token_hex(4)
        self.name = f"avk-restore-{self.tag}"
        self.volume = f"avk-restore-vol-{self.tag}"
        self.password = _secrets.token_hex(12)

    def start(self) -> None:
        sh(["docker", "network", "create", "--internal", RESTORE_NETWORK])  # exists already: fine
        sh(["docker", "volume", "create", self.volume])
        made = run(["docker", "run", "-d", "--name", self.name, "--network", RESTORE_NETWORK, "-v", f"{self.volume}:/var/lib/postgresql/data",
                    "--label", "avk.restore=1", "--memory", "768m", "-e", "POSTGRES_PASSWORD", PG_IMAGE],
                   env={"POSTGRES_PASSWORD": self.password})
        if made.returncode != 0:
            raise BackupError(f"cannot start the restore container: {_last(made.stderr)}")
        for _ in range(60):
            if sh(["docker", "exec", self.name, "pg_isready", "-U", "postgres"]).returncode == 0:
                time.sleep(2)  # the image restarts once after init; wait for the second start
                if sh(["docker", "exec", self.name, "pg_isready", "-U", "postgres"]).returncode == 0:
                    return
            time.sleep(1)
        raise BackupError("the restore container did not become ready")

    def psql(self, db: str, sql: str):
        return sh(["docker", "exec", "-i", self.name, "psql", "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-tAX"], stdin=sql)

    def remove(self) -> None:
        sh(["docker", "rm", "-f", "-v", self.name])
        sh(["docker", "volume", "rm", "-f", self.volume])


COUNT_SQL = """select format('select %L, count(*) from %I.%I', schemaname||'.'||tablename, schemaname, tablename)
from pg_tables where schemaname not in ('pg_catalog','information_schema') order by 1\\gexec"""


def row_counts(psql_fn, db: str) -> dict[str, int]:
    done = psql_fn(db, COUNT_SQL)
    if done.returncode != 0:
        raise BackupError(f"row count query failed on {db}: {_last(done.stderr)}")
    counts = {}
    for line in done.stdout.splitlines():
        name, _, n = line.rpartition("|")
        if name:
            counts[name] = int(n)
    return counts


def prod_psql(db: str, sql: str):
    return sh(["docker", "exec", "-i", PROD_PG, "psql", "-U", "postgres", "-d", db, "-tAX", "-v", "ON_ERROR_STOP=1"], stdin=sql)


def compare(prod: dict[str, int], restored: dict[str, int]) -> tuple[list[str], list[str]]:
    """(problems, notes). Rows added to prod after the backup are a note; missing tables or extra rows are problems."""
    problems, notes = [], []
    for table, n in prod.items():
        if table not in restored:
            problems.append(f"{table}: missing in the restore")
        elif restored[table] > n:
            problems.append(f"{table}: restore has {restored[table]} rows, prod only {n}")
        elif restored[table] < n:
            notes.append(f"{table}: prod has {n - restored[table]} newer row(s) than the backup")
    return problems, notes


def restore_snapshot(restic: Restic, repo: Repo, target: Path) -> Path:
    got = restic.call(repo, ["restore", "latest", "--tag", "avk", "--target", str(target)])
    if got.returncode != 0:
        raise BackupError(f"restic restore from {repo.name} failed: {_last(got.stderr)}")
    found = list(target.rglob("globals.sql"))
    if not found:
        raise BackupError(f"the {repo.name} snapshot has no database dumps")
    return found[0].parent


def load_into(scratch: Scratch, dump_dir: Path, db: str) -> None:
    scratch.psql("postgres", f'create database "{db}"')
    cp = sh(["docker", "cp", str(dump_dir / f"{db}.dump"), f"{scratch.name}:/tmp/{db}.dump"])
    if cp.returncode != 0:
        raise BackupError(f"docker cp failed: {_last(cp.stderr)}")
    done = sh(["docker", "exec", scratch.name, "pg_restore", "-U", "postgres", "-d", db, "--no-owner", "--no-privileges", f"/tmp/{db}.dump"])
    if done.returncode != 0:
        raise BackupError(f"pg_restore of {db} failed: {_last(done.stderr)}")


def verify(settings: Settings | None = None, restic: Restic | None = None) -> int:
    settings = settings or load_settings()
    restic = restic or Restic(settings)
    bad = 0
    for repo in repos(settings):
        work = config.avk_home() / "backups" / f"verify-{int(time.time())}-{repo.name}"
        scratch = Scratch()
        try:
            restic.ensure_repo(repo)
            if (chk := restic.call(repo, ["check"])).returncode != 0:
                raise BackupError(f"restic check failed on {repo.name}: {_last(chk.stderr)}")
            dump_dir = restore_snapshot(restic, repo, work)
            scratch.start()
            dbs = sorted(p.stem for p in dump_dir.glob("*.dump"))
            if not dbs:
                raise BackupError("no databases in the snapshot")
            for db in dbs:
                load_into(scratch, dump_dir, db)
                problems, notes = compare(row_counts(prod_psql, db), row_counts(scratch.psql, db))
                for n in notes:
                    print(f"note  {db}: {n}")
                if problems:
                    raise BackupError(f"{db}: " + "; ".join(problems))
                total = sum(row_counts(scratch.psql, db).values())
                print(f"ok    {repo.name}: {db} restored, row counts match prod ({total} rows)")
        except (BackupError, OSError, subprocess.TimeoutExpired) as exc:
            bad += 1
            alert(f"BACKUP VERIFY FAILED ({repo.name}): {exc}")
        finally:
            scratch.remove()
            wipe(work)
    return 1 if bad else 0


# ------------------------------------------------------------------------------------------------------- restore
def restore(app: str, source: str, to: str, confirm: str | None, settings: Settings | None = None) -> int:
    settings = settings or load_settings()
    restic = Restic(settings)
    candidates = {r.name: r for r in repos(settings)}
    if source not in candidates:
        print(f"fail  '{source}' is not configured (available: {', '.join(candidates)})")
        return 1
    repo = candidates[source]
    work = config.avk_home() / "backups" / f"restore-{int(time.time())}"
    try:
        restic.ensure_repo(repo)
        dump_dir = restore_snapshot(restic, repo, work)
        if not (dump_dir / f"{app}.dump").is_file():
            print(f"fail  no dump for {app} in the {source} snapshot")
            return 1
        if to == "scratch":
            scratch = Scratch(tag=f"scratch-{app}")
            scratch.remove()
            scratch.start()
            load_into(scratch, dump_dir, app)
            print(f"ok    {app} restored into container {scratch.name} (no host port). Inspect: "
                  f"docker exec -it {scratch.name} psql -U postgres -d {app}\n      Remove: docker rm -f -v {scratch.name}")
            return 0
        phrase = f"restore {app} to prod"
        if confirm != phrase:
            print(f'fail  restoring INTO prod replaces its data. Re-run with --confirm "{phrase}"')
            return 1
        fresh = config.avk_home() / "backups" / f"pre-restore-{app}.dump"
        fresh.parent.mkdir(parents=True, exist_ok=True)
        dump_database(app, fresh)
        print(f"ok    safety dump of current prod {app} saved to {fresh}")
        sh(["docker", "cp", str(dump_dir / f"{app}.dump"), f"{PROD_PG}:/tmp/{app}.restore"])
        done = sh(["docker", "exec", PROD_PG, "pg_restore", "-U", "postgres", "-d", app, "--clean", "--if-exists", "--no-owner", f"/tmp/{app}.restore"])
        sh(["docker", "exec", PROD_PG, "rm", "-f", f"/tmp/{app}.restore"])
        if done.returncode != 0:
            print(f"fail  pg_restore reported: {_last(done.stderr)} (the safety dump is at {fresh})")
            return 1
        from avk import dbadmin

        if app == "keycloak":
            dbadmin.create_keycloak("prod")          # the login database has one owner role, not the three roles an app gets
        else:
            dbadmin.create(app, "prod")  # re-apply ownership and privileges after the restore
        print(f"ok    {app} restored into prod from the {source} snapshot")
        return 0
    except (BackupError, OSError, subprocess.TimeoutExpired) as exc:
        alert(f"RESTORE FAILED: {exc}")
        return 1
    finally:
        wipe(work)


def main(args) -> int:
    from avk import heartbeat                              # success pings the outside "dead man's switch" (a no-op until configured)

    try:
        if args.action == "run":
            code = run_backup()
            if code == 0:
                heartbeat.ping("backup-run")
            return code
        if args.action == "verify":
            code = verify()
            if code == 0:
                heartbeat.ping("backup-verify")
            return code
        return restore(args.app, args.source, args.to, args.confirm)
    except BackupError as exc:
        alert(f"{args.action.upper()} FAILED: {exc}")
        return 1
