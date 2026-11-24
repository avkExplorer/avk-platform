"""`avk tf`: run Terraform for infra/terraform inside Docker.

Why Docker: on this PC Norton intercepts the TLS that Terraform uses to talk to its own provider plugins on
localhost ("x509: certificate signed by unknown authority"), so host Terraform cannot load any provider.
Inside a container that traffic is out of Norton's reach; outbound HTTPS uses the exported Windows roots.

Secrets come from %AVK_HOME%\\secrets\\platform\\cloudflare.env and reach the container as inherited
environment variables (`-e NAME` with no value), so they never appear on a command line or in logs.
`apply` only applies the plan file written by the last `plan`, so what runs is exactly what was reviewed.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from avk import config

IMAGE = "hashicorp/terraform:1.17.1"
TF_DIR = config.REPO_ROOT / "infra" / "terraform"
PLAN_NAME = "cloudflare.tfplan"
PLAN_MAX_AGE_S = 3600
REQUIRED_KEYS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ZONE_ID")


def env_file() -> Path:
    return config.avk_home() / "secrets" / "platform" / "cloudflare.env"


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_secrets(path: Path | None = None) -> dict[str, str]:
    path = path or env_file()
    if not path.is_file():
        raise SystemExit(f"fail  {path} not found: create it from runtime/secrets.example/cloudflare.env.example")
    values = parse_env_file(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_KEYS if not values.get(k) or values[k] == "replace-me"]
    if missing:
        raise SystemExit(f"fail  {path.name} is missing: {', '.join(missing)}")
    return values


def owner_vars() -> dict[str, str]:
    """Private values (mailbox addresses) from secrets/platform/owner.tfvars, passed as TF_VAR_<name>."""
    path = config.avk_home() / "secrets" / "platform" / "owner.tfvars"
    if not path.is_file():
        return {}
    return {f"TF_VAR_{k}": v for k, v in parse_env_file(path.read_text(encoding="utf-8")).items()
            if v and "example.com" not in v}


def container_env(secrets: dict[str, str]) -> dict[str, str]:
    """Variables passed into the container by name only."""
    return {**owner_vars(),
        "CLOUDFLARE_API_TOKEN": secrets["CLOUDFLARE_API_TOKEN"],
        "TF_VAR_account_id": secrets["CLOUDFLARE_ACCOUNT_ID"],
        "TF_VAR_zone_id": secrets["CLOUDFLARE_ZONE_ID"],
        "SSL_CERT_FILE": "/certs/host-roots.pem",
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
        "TF_DATA_DIR": "/state/.terraform",
        "TF_PLUGIN_CACHE_DIR": "/state/plugin-cache",
    }



def docker_argv(tf_args: list[str], env_names: list[str]) -> list[str]:
    home = config.avk_home()
    argv = ["docker", "run", "--rm", "-i",
            "-v", f"{config.REPO_ROOT}:/repo",
            "-v", f"{home / 'tfstate'}:/state",
            "-v", f"{home / 'certs' / 'host-roots.pem'}:/certs/host-roots.pem:ro",
            "-w", "/repo/infra/terraform"]
    for name in env_names:
        argv += ["-e", name]  # value inherited from our environment, never written on the command line
    return argv + [IMAGE, *tf_args]


def terraform(tf_args: list[str], secrets: dict[str, str], capture: bool = False) -> subprocess.CompletedProcess:
    env_vars = container_env(secrets)
    (config.avk_home() / "tfstate" / "plugin-cache").mkdir(parents=True, exist_ok=True)
    return subprocess.run(docker_argv(tf_args, list(env_vars)), env={**os.environ, **env_vars},
                          text=True, capture_output=capture, encoding="utf-8", errors="replace")


def plan_file() -> Path:
    return config.avk_home() / "tfstate" / PLAN_NAME


def write_tunnel_token(secrets: dict[str, str]) -> None:
    out = terraform(["output", "-raw", "tunnel_token"], secrets, capture=True)
    if out.returncode != 0 or not out.stdout.strip():
        print("warn  could not read the tunnel token output")
        return
    path = config.avk_home() / "secrets" / "platform" / "tunnel.token"
    path.write_text(out.stdout.strip(), encoding="ascii")
    print(f"ok    tunnel token written to {path}")


def main(args) -> int:
    secrets = load_secrets()
    action = args.action
    if action in ("init", "plan", "apply", "validate", "output"):
        init = terraform(["init", "-no-color", "-upgrade=false"], secrets, capture=True)
        if init.returncode != 0:
            print(init.stdout + init.stderr)
            return init.returncode
        if action == "init":
            print("ok    terraform init")
            return 0

    if action == "validate":
        return terraform(["validate", "-no-color"], secrets).returncode
    if action == "fmt":
        return terraform(["fmt", "-check", "-diff", "-no-color"], secrets).returncode
    if action == "output":
        return terraform(["output", "-no-color"], secrets).returncode
    if action == "plan":
        code = terraform(["plan", "-no-color", "-detailed-exitcode", f"-out=/state/{PLAN_NAME}"], secrets).returncode
        if code == 0:
            print("\nok    no changes")
            return 0
        if code == 2:
            print(f"\nplan saved. Review it, then run: avk tf apply   (valid for {PLAN_MAX_AGE_S // 60} minutes)")
            return 0
        return code
    if action == "apply":
        plan = plan_file()
        if not plan.is_file():
            print("fail  no saved plan: run `avk tf plan` first")
            return 1
        if time.time() - plan.stat().st_mtime > PLAN_MAX_AGE_S:
            print("fail  saved plan is older than an hour: run `avk tf plan` again")
            return 1
        if not args.yes:
            print("fail  apply changes live DNS/edge settings: re-run with --yes after reviewing the plan")
            return 1
        code = terraform(["apply", "-no-color", f"/state/{PLAN_NAME}"], secrets).returncode
        plan.unlink(missing_ok=True)
        if code == 0:
            write_tunnel_token(secrets)
        return code
    print(f"fail  unknown action {action}")
    return 1
