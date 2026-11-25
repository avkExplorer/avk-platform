"""A Java truststore (PKCS12) built from the exported Windows roots, for app containers on this PC.

Why: Norton re-signs outgoing TLS (including the mail connection to Resend) with its own root, which only Windows trusts. A Java app
in a container fails with "PKIX path building failed" until it trusts that root too. The roots are public certificates; the
password below is the conventional one and protects nothing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

PASSWORD = "changeit"
CONTAINER_PATH = "/certs/host-truststore.p12"
PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)

Runner = Callable[[list[str]], int]


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, capture_output=True, timeout=60).returncode


def find_keytool() -> str | None:
    return shutil.which("keytool")


def build(pem: Path, out: Path, keytool: str | None = None, run: Runner = _run) -> tuple[int, int]:
    """Import every certificate in `pem` into a fresh PKCS12 file at `out`. Returns (imported, skipped)."""
    keytool = keytool or find_keytool()
    if not keytool:
        raise SystemExit("fail  keytool not found (install a JDK, or put it on PATH)")
    certs = PEM_RE.findall(pem.read_text(encoding="ascii", errors="ignore"))
    if not certs:
        raise SystemExit(f"fail  no certificates in {pem}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out.with_suffix(".tmp")
    tmp_out.unlink(missing_ok=True)
    imported = skipped = 0
    with tempfile.TemporaryDirectory() as folder:
        for i, cert in enumerate(certs):
            one = Path(folder) / f"c{i}.pem"
            one.write_text(cert + "\n", encoding="ascii")
            code = run([keytool, "-importcert", "-noprompt", "-alias", f"root{i}", "-file", str(one), "-keystore", str(tmp_out),
                        "-storetype", "PKCS12", "-storepass", PASSWORD])
            imported, skipped = (imported + 1, skipped) if code == 0 else (imported, skipped + 1)
    if imported == 0:
        tmp_out.unlink(missing_ok=True)
        raise SystemExit("fail  no certificate could be imported")
    tmp_out.replace(out)                                   # swap in only a complete file
    return imported, skipped


def java_options() -> str:
    return f"-Djavax.net.ssl.trustStore={CONTAINER_PATH} -Djavax.net.ssl.trustStorePassword={PASSWORD} -Djavax.net.ssl.trustStoreType=PKCS12"
