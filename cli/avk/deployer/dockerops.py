"""Docker operations for the deployer, via the docker CLI. App containers are run with fixed hardening; none of them
gets the Docker socket, a host folder, or a published port (Traefik reaches them on the private avk-prod network)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

NETWORK = "avk-prod"
# Small pinned image used only to make HTTP requests from inside the avk-prod network (smoke tests, readiness).
CURL_IMAGE = "curlimages/curl@sha256:3fb7e13e5d179a845c97d78204f3f1064d1907037f26a3ebf91710571151fd93"

Runner = Callable[[list[str], str | None], tuple[int, str, str]]


def _run(argv: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"


def container_name(app: str, color: str) -> str:
    return f"{app}-{color}"


class Docker:
    def __init__(self, run: Runner | None = None, curl_image: str = CURL_IMAGE):
        self._run = run or _run
        self.curl_image = curl_image

    def pull(self, ref: str) -> tuple[bool, str]:
        code, _, err = self._run(["docker", "pull", "-q", ref], None)
        return code == 0, (err.strip().splitlines() or [""])[-1][:200]

    def repo_digests(self, ref: str) -> str:
        _, out, _ = self._run(["docker", "image", "inspect", ref, "--format", "{{json .RepoDigests}}"], None)
        return out.strip()

    def exists(self, name: str) -> bool:
        code, _, _ = self._run(["docker", "container", "inspect", name], None)
        return code == 0

    def remove(self, name: str) -> None:
        self._run(["docker", "rm", "-f", name], None)

    def stop(self, name: str) -> None:
        self._run(["docker", "stop", "-t", "20", name], None)
        self._run(["docker", "rm", name], None)

    def run_migration(self, ref: str, env_file: Path, command: list[str]) -> tuple[int, str]:
        """One-shot container with the migrator credentials; those credentials go to no other container."""
        argv = ["docker", "run", "--rm", "--network", NETWORK, "--env-file", str(env_file), "--read-only",
                "--tmpfs", "/tmp:rw,nosuid,size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--memory", "512m", "--cpus", "1", "--pids-limit", "256", "--entrypoint", command[0], ref, *command[1:]]
        code, out, err = self._run(argv, None)
        return code, (out + err).strip()[-400:]

    def start_app(self, name: str, ref: str, env_file: Path, labels: dict[str, str]) -> tuple[bool, str]:
        self.remove(name)
        argv = ["docker", "run", "-d", "--name", name, "--network", NETWORK, "--restart", "unless-stopped",
                "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--memory", "512m", "--cpus", "1", "--pids-limit", "256",
                "--env-file", str(env_file), "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3"]
        for k, v in labels.items():
            argv += ["--label", f"{k}={v}"]
        code, _, err = self._run(argv + [ref], None)
        return code == 0, (err.strip().splitlines() or [""])[-1][:200]

    def curl(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
        """GET from a throwaway container on the avk-prod network. Returns (HTTP status or 0, body)."""
        argv = ["docker", "run", "--rm", "--network", NETWORK, "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--memory", "64m", self.curl_image,
                "-s", "--max-time", "10", "-w", "\n%{http_code}"]
        for k, v in (headers or {}).items():
            argv += ["-H", f"{k}: {v}"]
        code, out, _ = self._run(argv + [url], None)
        if code != 0 or "\n" not in out:
            return 0, ""
        body, _, status = out.rpartition("\n")
        return (int(status) if status.strip().isdigit() else 0), body
