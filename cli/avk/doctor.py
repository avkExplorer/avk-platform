"""`avk doctor`: is this PC ready to run the platform? Each check returns ok / warn / fail with a fix."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from avk import config

OK, WARN, FAIL = "ok", "warn", "fail"

# Tools the platform shells out to: name -> (version argv, phase that first needs it).
TOOLS = {
    "git": (["git", "--version"], 0),
    "docker": (["docker", "--version"], 0),
    "gh": (["gh", "--version"], 3),
    "terraform": (["terraform", "version"], 1),
    "cosign": (["cosign", "version"], 3),
    "restic": (["restic", "version"], 4),
    "aws": (["aws", "--version"], 9),
}

TLS_PROBES = ["https://api.github.com/zen", "https://registry.terraform.io/.well-known/terraform.json", "https://ghcr.io/v2/"]
LOOSE_ACL_PRINCIPALS = ("Everyone", "BUILTIN\\Users", "NT AUTHORITY\\Authenticated Users")


@dataclass
class Result:
    status: str
    name: str
    detail: str


def run(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    """Run a command (argv list, no shell). Returns (exit code, combined output); 127 when not found."""
    exe = shutil.which(argv[0])
    if not exe:
        return 127, f"{argv[0]} not found on PATH"
    try:
        proc = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ---------------------------------------------------------------------------------------------- parsers


def first_line(text: str) -> str:
    return text.splitlines()[0].strip() if text.strip() else ""


def parse_powercfg_ac(output: str) -> int | None:
    """Seconds from `powercfg /query ...` "Current AC Power Setting Index: 0x...". None if absent."""
    m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", output)
    return int(m.group(1), 16) if m else None


def loose_acl_entries(icacls_output: str) -> list[str]:
    return [p for p in LOOSE_ACL_PRINCIPALS if p.lower() + ":" in icacls_output.lower()]


def wsl_memory_cap(text: str) -> str | None:
    in_wsl2 = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_wsl2 = line.lower() == "[wsl2]"
        elif in_wsl2 and line.lower().startswith("memory"):
            return line.split("=", 1)[1].strip() if "=" in line else None
    return None


# ----------------------------------------------------------------------------------------------- checks


def check_python() -> Result:
    import sys
    v = sys.version_info
    status = OK if v >= (3, 12) else FAIL
    return Result(status, "python", f"{v.major}.{v.minor}.{v.micro}" + ("" if status == OK else ": need 3.12+"))


def check_tools() -> list[Result]:
    out = []
    for name, (argv, phase) in TOOLS.items():
        code, text = run(argv)
        if code == 0:
            line = next((ln for ln in text.splitlines() if "GitVersion" in ln), first_line(text))
            out.append(Result(OK, f"tool {name}", re.sub(r"\s+", " ", line)))
        else:
            status = FAIL if phase == 0 else WARN
            out.append(Result(status, f"tool {name}", f"missing (needed from Phase {phase}): winget install --source winget"))
    return out


def check_avk_home() -> list[Result]:
    home = config.avk_home()
    if not os.environ.get("AVK_HOME"):
        return [Result(FAIL, "AVK_HOME", "user environment variable not set; see Phase 0 step 4")]
    if not home.is_dir():
        return [Result(FAIL, "AVK_HOME", f"{home} does not exist")]
    results = [Result(OK, "AVK_HOME", str(home))]
    missing = [d for d in ("secrets", "tfstate", "state", "backups", "logs", "certs", "private") if not (home / d).is_dir()]
    if missing:
        results.append(Result(FAIL, "AVK_HOME layout", f"missing folders: {', '.join(missing)}"))
    code, text = run(["icacls", str(home)])
    loose = loose_acl_entries(text) if code == 0 else []
    if code != 0:
        results.append(Result(WARN, "AVK_HOME access", "could not read permissions"))
    elif loose:
        results.append(Result(FAIL, "AVK_HOME access", f"readable by {', '.join(loose)}: icacls {home} /inheritance:r"))
    else:
        results.append(Result(OK, "AVK_HOME access", "owner and SYSTEM only"))
    return results


def check_certs() -> list[Result]:
    pem = config.avk_home() / "certs" / "host-roots.pem"
    results = []
    if not pem.is_file():
        results.append(Result(FAIL, "CA bundle", f"{pem} missing: avk certs refresh"))
    else:
        age_days = (time.time() - pem.stat().st_mtime) / 86400
        results.append(Result(OK if age_days < 30 else WARN, "CA bundle",
                              f"{age_days:.0f} days old" + ("" if age_days < 30 else ": avk certs refresh")))
    aws_ca = os.environ.get("AWS_CA_BUNDLE")
    results.append(Result(OK if aws_ca and Path(aws_ca).is_file() else WARN, "AWS_CA_BUNDLE",
                          aws_ca or "not set; the AWS CLI fails behind Norton without it"))
    return results


def check_tls() -> list[Result]:
    ctx = ssl.create_default_context()
    out = []
    for url in TLS_PROBES:
        host = url.split("/")[2]
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "avk-doctor"}),
                                        timeout=10, context=ctx):
                out.append(Result(OK, f"https {host}", "reachable"))
        except urllib.error.HTTPError:
            out.append(Result(OK, f"https {host}", "reachable (HTTP error is fine)"))
        except Exception as exc:  # noqa: BLE001 - report any network failure as a check result
            out.append(Result(FAIL, f"https {host}", f"{type(exc).__name__}: {exc}"[:160]))
    return out


def check_docker() -> list[Result]:
    code, text = run(["docker", "info", "--format", "{{.ServerVersion}} {{.MemTotal}}"], timeout=20)
    if code != 0:
        return [Result(FAIL, "docker engine", "not running: start Docker Desktop")]
    version, _, mem = text.partition(" ")
    mem_gb = f"{int(mem) / 2**30:.1f} GB" if mem.isdigit() else "?"
    results = [Result(OK, "docker engine", f"{version}, {mem_gb} available to containers")]
    wslconfig = Path(os.environ.get("USERPROFILE", "")) / ".wslconfig"
    cap = wsl_memory_cap(wslconfig.read_text(encoding="utf-8", errors="replace")) if wslconfig.is_file() else None
    results.append(Result(OK if cap else WARN, "WSL memory cap", cap or "none set in ~/.wslconfig"))
    return results


def check_disk() -> list[Result]:
    out = []
    for label, path in (("disk system", Path(os.environ.get("SystemDrive", "C:") + "\\")), ("disk AVK_HOME", config.avk_home())):
        if not path.exists():
            continue
        free_gb = shutil.disk_usage(path).free / 2**30
        status = FAIL if free_gb < 15 else WARN if free_gb < 50 else OK
        out.append(Result(status, label, f"{free_gb:.0f} GB free on {path.drive or path}"))
    return out


def check_ram() -> Result:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        total = stat.ullTotalPhys / 2**30
    except Exception:  # noqa: BLE001 - not Windows
        return Result(WARN, "ram", "could not read")
    return Result(OK if total >= 16 else WARN, "ram", f"{total:.0f} GB" + ("" if total >= 16 else ": 16 GB+ recommended"))


def check_power() -> list[Result]:
    out = []
    for label, setting in (("sleep on AC", "STANDBYIDLE"), ("hibernate on AC", "HIBERNATEIDLE")):
        code, text = run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", setting])
        seconds = parse_powercfg_ac(text) if code == 0 else None
        if seconds is None:
            out.append(Result(WARN, label, "could not read"))
        elif seconds == 0:
            out.append(Result(OK, label, "never"))
        else:
            out.append(Result(FAIL, label, f"after {seconds // 60} min: powercfg /change standby-timeout-ac 0"))
    return out


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_ports() -> list[Result]:
    try:
        app_ports = {a["stg_port"]: f"stg {a['name']}" for a in config.load().apps if isinstance(a.get("stg_port"), int)}
    except Exception:  # noqa: BLE001 - validate reports platform.yaml problems
        app_ports = {}
    busy = [f"{p} ({what})" for p, what in {**config.PLATFORM_PORTS, **app_ports}.items() if port_in_use(p)]
    if not busy:
        return [Result(OK, "platform ports", "all free")]
    # Before Phase 4 nothing of ours listens, so a busy port is another program's.
    return [Result(WARN, "platform ports", "in use: " + ", ".join(busy) + " (fine if it is the platform's own stack)")]


def check_git_and_gh() -> list[Result]:
    out = []
    # Repo-local or global identity both count; commits here go to a public repo, so prefer a noreply email.
    code, name = run(["git", "-C", str(config.REPO_ROOT), "config", "user.name"])
    code2, email = run(["git", "-C", str(config.REPO_ROOT), "config", "user.email"])
    ok = code == 0 and code2 == 0 and bool(name) and bool(email)
    detail = (f"{name} <{email}>" if email.endswith("@users.noreply.github.com") else f"{name} (email is not a GitHub noreply address; it will be public)") if ok \
        else "git config user.name / user.email not set"
    out.append(Result(OK if ok else WARN, "git identity", detail))
    code, _ = run(["gh", "auth", "status"])
    out.append(Result(OK if code == 0 else WARN, "gh auth", "logged in" if code == 0 else "run: gh auth login (needed from Phase 3)"))
    return out


CHECKS: list[Callable[[], Result | list[Result]]] = [
    check_python, check_tools, check_avk_home, check_certs, check_tls, check_docker,
    check_disk, check_ram, check_power, check_ports, check_git_and_gh,
]


def run_checks(checks: list[Callable[[], Result | list[Result]]] | None = None) -> list[Result]:
    results: list[Result] = []
    for check in checks or CHECKS:
        try:
            r = check()
        except Exception as exc:  # noqa: BLE001 - a crashing check must not hide the others
            r = Result(FAIL, check.__name__.removeprefix("check_"), f"check crashed: {exc}")
        results.extend(r if isinstance(r, list) else [r])
    return results


def main(_args=None) -> int:
    results = run_checks()
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{r.status:<4}  {r.name:<{width}}  {r.detail}")
    fails = sum(r.status == FAIL for r in results)
    warns = sum(r.status == WARN for r in results)
    print(f"\n{len(results)} checks: {fails} fail, {warns} warn")
    return 1 if fails else 0
