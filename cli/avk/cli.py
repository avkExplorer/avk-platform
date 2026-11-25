"""Entry point: `python -m avk <command>`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import ssl
import sys

from avk import __version__, auth, backup, config, dbadmin, doctor, landing, mail, prod, secretgen, stg, tf
from avk.deployer import agent


def cmd_validate(_args) -> int:
    try:
        platform = config.load()
    except Exception as exc:  # noqa: BLE001 - surface any parse problem to the user
        print(f"fail  {config.PLATFORM_FILE.name}: {exc}")
        return 1
    errors, warnings = config.validate(platform)
    for w in warnings:
        print(f"warn  {w}")
    for e in errors:
        print(f"fail  {e}")
    if errors:
        return 1
    print(f"ok    {config.PLATFORM_FILE.name}: {len(platform.apps)} app(s), domain {platform.domain}")
    return 0


def cmd_certs_refresh(_args) -> int:
    """Export the Windows trusted roots (incl. Norton's) to a PEM for tools and containers."""
    pem = config.avk_home() / "certs" / "host-roots.pem"
    pem.parent.mkdir(parents=True, exist_ok=True)
    certs = [ssl.DER_cert_to_PEM_cert(c) for store in ("ROOT", "CA")
             for c, enc, _ in ssl.enum_certificates(store) if enc == "x509_asn"]  # type: ignore[attr-defined]
    pem.write_text("".join(dict.fromkeys(certs)), encoding="ascii")
    print(f"ok    wrote {len(set(certs))} certificates to {pem}")
    return 0


def _log(event: str, **fields) -> None:
    logs = config.avk_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    line = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
    with (logs / "avk.log").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="avk", description="AVK app platform tool")
    p.add_argument("--version", action="version", version=f"avk {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check this PC is ready").set_defaults(func=doctor.main)
    sub.add_parser("validate", help="check platform.yaml").set_defaults(func=cmd_validate)

    certs = sub.add_parser("certs", help="CA bundle for tools behind Norton").add_subparsers(dest="action", required=True)
    certs.add_parser("refresh").set_defaults(func=cmd_certs_refresh)

    tfp = sub.add_parser("tf", help="Terraform (in Docker) for the Cloudflare edge")
    tfp.add_argument("action", choices=["init", "validate", "fmt", "plan", "apply", "output"])
    tfp.add_argument("--yes", action="store_true", help="apply: confirm the reviewed saved plan")
    tfp.set_defaults(func=tf.main)

    prodp = sub.add_parser("prod", help="prod stack (tunnel + Traefik)").add_subparsers(dest="action", required=True)
    for action in ("up", "down", "status"):
        prodp.add_parser(action).set_defaults(func=prod.main)

    secp = sub.add_parser("secrets", help="generate credentials outside git (values are never printed)").add_subparsers(dest="action", required=True)
    secp.add_parser("platform", help="platform's own secrets (database admin, backup password)").set_defaults(func=secretgen.main)
    sinit = secp.add_parser("init", help="an app's secret files for one environment")
    sinit.add_argument("app")
    sinit.add_argument("--env", choices=["stg", "prod"], required=True)
    sinit.add_argument("--rotate", action="append", choices=["runtime", "migrator", "ro"], help="replace that password")
    sinit.set_defaults(func=secretgen.main)

    dbp = sub.add_parser("db", help="app databases and roles").add_subparsers(dest="action", required=True)
    dcreate = dbp.add_parser("create", help="create the database and its three least-privilege roles")
    dcreate.add_argument("app")
    dcreate.add_argument("--env", choices=["stg", "prod"], required=True)
    dcreate.set_defaults(func=dbadmin.main)

    sub.add_parser("deploy-agent", help="poll GitHub once and deploy any approved release").set_defaults(func=agent.cmd_agent)
    depp = sub.add_parser("deploy", help="deploy an app now (same trust checks and smoke tests as the agent)")
    depp.add_argument("app")
    depp.add_argument("--digest", help="deploy this exact digest (still must be signed and approved)")
    depp.add_argument("--force", action="store_true", help="redeploy the live digest into fresh containers (after rotating a secret)")
    depp.set_defaults(func=agent.cmd_deploy)
    rbp = sub.add_parser("rollback", help="return an app to its previous digest (all trust checks run again)")
    rbp.add_argument("app")
    rbp.set_defaults(func=agent.cmd_rollback)

    bkp = sub.add_parser("backup", help="encrypted backups (external drive + off-site) and restore tests").add_subparsers(dest="action", required=True)
    bkp.add_parser("run", help="back up prod databases, secrets, tfstate and state").set_defaults(func=backup.main)
    bkp.add_parser("verify", help="restic check, then restore into a throwaway Postgres and compare row counts").set_defaults(func=backup.main)
    brs = bkp.add_parser("restore", help="restore one app's database from a backup")
    brs.add_argument("app")
    brs.add_argument("--from", dest="source", choices=["local", "offsite"], required=True)
    brs.add_argument("--to", choices=["scratch", "prod"], required=True)
    brs.add_argument("--confirm", help='typed confirmation for --to prod: "restore <app> to prod"')
    brs.set_defaults(func=backup.main)

    landp = sub.add_parser("landing", help="apex/status placeholder pages on Cloudflare Pages").add_subparsers(dest="action", required=True)
    landp.add_parser("deploy").set_defaults(func=landing.main)

    mailp = sub.add_parser("mail", help="business email DNS checks").add_subparsers(dest="action", required=True)
    mailp.add_parser("check").set_defaults(func=mail.main)
    tsp = mailp.add_parser("test-send", help="send ONE real test email (needs --yes)")
    tsp.add_argument("--to", required=True)
    tsp.add_argument("--yes", action="store_true")
    tsp.set_defaults(func=mail.test_send)

    stgp = sub.add_parser("stg", help="stg stack on 127.0.0.1 (pg-stg, Mailpit, index, apps)").add_subparsers(dest="action", required=True)
    for action in ("up", "down", "status"):
        a = stgp.add_parser(action)
        a.add_argument("app", nargs="?")
        if action == "up":
            a.add_argument("--build", action="store_true", help="rebuild the app from its sibling folder")
            a.add_argument("--digest", help="run a published image by digest instead of a local build")
        a.set_defaults(func=stg.main)

    authp = sub.add_parser("auth", help="shared Keycloak login (image, database, realm, clients)").add_subparsers(dest="action", required=True)
    authp.add_parser("build", help="build the local Keycloak image").set_defaults(func=auth.main)
    ainit = authp.add_parser("init", help="Keycloak secrets and database for one environment")
    ainit.add_argument("--env", choices=["stg", "prod"], required=True)
    ainit.set_defaults(func=auth.main)
    aapply = authp.add_parser("apply", help="apply the realm file to the running Keycloak (idempotent)")
    aapply.add_argument("--env", choices=["stg", "prod"], required=True)
    aapply.add_argument("--refresh-secrets", action="store_true", help="also re-send the SMTP password (after rotating the mail key)")
    aapply.set_defaults(func=auth.main)
    aclient = authp.add_parser("client", help="create or update an app's login client and write its secret")
    aclient.add_argument("app")
    aclient.add_argument("--env", choices=["stg", "prod"], required=True)
    aclient.add_argument("--rotate", action="store_true", help="issue a new client secret")
    aclient.set_defaults(func=auth.main)

    seedp = sub.add_parser("seed", help="load synthetic data into stg (never prod)")
    seedp.add_argument("app")
    seedp.add_argument("--env", default="stg")
    seedp.set_defaults(func=stg.seed)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
