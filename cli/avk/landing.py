"""`avk landing deploy`: upload landing/ to the Cloudflare Pages project `avk-landing`.

Runs wrangler inside a Node container (host TLS is intercepted by Norton; the container gets the exported
Windows roots via NODE_EXTRA_CA_CERTS). The API token reaches the container by name only, never on a command line.
"""

from __future__ import annotations

import os
import subprocess

from avk import config, tf

IMAGE = "node:22-slim"
PROJECT = "avk-landing"
SITE_DIR = config.REPO_ROOT / "landing"


def docker_argv() -> list[str]:
    home = config.avk_home()
    return ["docker", "run", "--rm", "-i",
            "-v", f"{SITE_DIR}:/site:ro",
            "-v", f"{home / 'certs' / 'host-roots.pem'}:/certs/host-roots.pem:ro",
            "-e", "CLOUDFLARE_API_TOKEN", "-e", "CLOUDFLARE_ACCOUNT_ID",
            "-e", "NODE_EXTRA_CA_CERTS=/certs/host-roots.pem",
            "-e", "WRANGLER_SEND_METRICS=false", "-e", "CI=1",
            IMAGE, "npx", "--yes", "wrangler@4", "pages", "deploy", "/site",
            "--project-name", PROJECT, "--branch", "main", "--commit-dirty=true"]


def main(args) -> int:
    if not (SITE_DIR / "index.html").is_file():
        print(f"fail  {SITE_DIR / 'index.html'} not found")
        return 1
    secrets = tf.load_secrets()
    env = dict(os.environ,
               CLOUDFLARE_API_TOKEN=secrets["CLOUDFLARE_API_TOKEN"],
               CLOUDFLARE_ACCOUNT_ID=secrets["CLOUDFLARE_ACCOUNT_ID"])
    return subprocess.run(docker_argv(), env=env).returncode
