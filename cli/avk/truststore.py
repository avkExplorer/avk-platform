"""A Java truststore (PKCS12) built from the exported Windows roots, for app containers on this PC.

Why: Norton re-signs outgoing TLS (including the mail connection to Resend) with its own root, which only Windows trusts. A Java app
in a container fails with "PKIX path building failed" until it trusts that root too. The roots are public certificates; the
password below is the conventional one and protects nothing.
"""

from __future__ import annotations

import re
import shutil
import ssl
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


def export_roots(pem: Path) -> int:
    """Write the Windows trusted roots (including Norton's, if present) to a PEM file. Returns how many certificates."""
    certs = [ssl.DER_cert_to_PEM_cert(c) for store in ("ROOT", "CA")
             for c, enc, _ in ssl.enum_certificates(store) if enc == "x509_asn"]  # type: ignore[attr-defined]
    unique = list(dict.fromkeys(certs))
    pem.parent.mkdir(parents=True, exist_ok=True)
    pem.write_text("".join(unique), encoding="ascii")
    return len(unique)


def ensure_host_roots(certs_dir: Path, export: Callable[[Path], int] = export_roots) -> str | None:
    """prod's Keycloak mounts certs\host-roots.pem. If that file does not exist Docker creates a DIRECTORY with that name and the
    container then fails in a confusing way, so make the file first (a new machine has none). Returns an error text or None."""
    pem = certs_dir / "host-roots.pem"
    if pem.is_file():
        return None
    if pem.is_dir():                                  # a previous run mounted it before the file existed
        try:
            pem.rmdir()
        except OSError:
            return f"{pem} is a folder (Docker made it); delete it and run `avk certs refresh`"
    try:
        certs_dir.mkdir(parents=True, exist_ok=True)
        export(pem)
    except (OSError, AttributeError, ssl.SSLError) as exc:
        return f"cannot create {pem}: {exc}; run `avk certs refresh` on Windows"
    return None
