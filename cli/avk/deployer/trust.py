"""The four trust checks (specs/00-architecture.md, "Trust chain"). Every failure means "do not deploy".

1. Registered   the app is in platform.yaml, the trigger names that app, and the image is exactly ghcr.io/<owner>/<app>
2. Signed build `cosign verify` passes for the build identity (release.yml) AND was made by the app's own repository
3. Approved     `cosign verify-attestation` passes for the approval identity (approve.yml) with the approval predicate
                type, from the app's own repository, and the predicate's app and digest match
4. Same digest  the digest pulled is the digest verified (checked by the caller after the pull, `same_digest`)

The identities and allowed refs come from the `signing:` block of platform.yaml and nowhere else; there are no wildcards.
Anything unexpected (a timeout, bad JSON, an empty answer) fails closed.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
Runner = Callable[[list[str]], tuple[int, str, str]]


@dataclass
class Trigger:
    app: str
    image: str
    digest: str
    sha: str | None = None
    deployment_id: int | None = None
    source: str = "deployment"


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    check: str = ""


def default_runner(argv: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"


def expected_image(owner: str, app: str) -> str:
    return f"ghcr.io/{owner.lower()}/{app}"


def signing_config(raw: dict) -> dict:
    s = raw.get("signing") or {}
    for key in ("issuer", "build_identity", "approval_identity", "approval_predicate_type", "allowed_refs"):
        if not s.get(key):
            raise SystemExit(f"fail  platform.yaml signing.{key} is missing")
    return s


def registered(platform_raw: dict, owner: str, t: Trigger) -> Verdict:
    apps = {str(a.get("name")): a for a in platform_raw.get("apps") or []}
    app = apps.get(t.app)
    if app is None:
        return Verdict(False, f"app '{t.app}' is not registered in platform.yaml", "registered")
    if app.get("status", "stg") == "retired":
        return Verdict(False, f"app '{t.app}' is retired", "registered")
    if t.image != expected_image(owner, t.app):
        return Verdict(False, f"image {t.image} is not {expected_image(owner, t.app)}", "registered")
    if not DIGEST_RE.match(t.digest or ""):
        return Verdict(False, "digest is not a sha256 digest", "registered")
    if not app.get("repo"):
        return Verdict(False, f"app '{t.app}' has no repo in platform.yaml", "registered")
    return Verdict(True, "", "registered")


def _identities(template: str, refs: list[str]) -> list[str]:
    return [template.replace("{ref}", ref) for ref in refs]


def signed_build(signing: dict, repo: str, t: Trigger, run: Runner) -> Verdict:
    ref = f"{t.image}@{t.digest}"
    last = "no allowed ref matched"
    for identity in _identities(signing["build_identity"], signing["allowed_refs"]):
        code, _, err = run(["cosign", "verify", ref, "--certificate-identity", identity,
                            "--certificate-oidc-issuer", signing["issuer"], "--certificate-github-workflow-repository", repo])
        if code == 0:
            return Verdict(True, "", "signed-build")
        last = (err.strip().splitlines() or ["cosign verify failed"])[-1][:200]
    return Verdict(False, f"no valid build signature from {repo}: {last}", "signed-build")


def approved(signing: dict, repo: str, t: Trigger, run: Runner) -> Verdict:
    ref = f"{t.image}@{t.digest}"
    want = t.digest.split(":", 1)[1]
    last = "no allowed ref matched"
    for identity in _identities(signing["approval_identity"], signing["allowed_refs"]):
        code, out, err = run(["cosign", "verify-attestation", ref, "--type", signing["approval_predicate_type"],
                              "--certificate-identity", identity, "--certificate-oidc-issuer", signing["issuer"],
                              "--certificate-github-workflow-repository", repo])
        if code != 0:
            last = (err.strip().splitlines() or ["cosign verify-attestation failed"])[-1][:200]
            continue
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                statement = json.loads(base64.b64decode(json.loads(line)["payload"]))
            except (ValueError, KeyError, TypeError):
                continue
            predicate = statement.get("predicate") or {}
            subjects = [s.get("digest", {}).get("sha256") for s in statement.get("subject") or []]
            if (statement.get("predicateType") == signing["approval_predicate_type"] and predicate.get("app") == t.app
                    and predicate.get("digest") == t.digest and want in subjects):
                return Verdict(True, "", "approved")
        last = "an attestation exists but its app or digest does not match this release"
    return Verdict(False, f"no approval attestation for {t.digest[:19]}: {last}", "approved")


def check(platform_raw: dict, owner: str, t: Trigger, run: Runner | None = None) -> Verdict:
    """Run the checks in order and stop at the first failure."""
    run = run or default_runner
    v = registered(platform_raw, owner, t)
    if not v.ok:
        return v
    repo = next(a["repo"] for a in platform_raw["apps"] if str(a.get("name")) == t.app)
    signing = signing_config(platform_raw)
    for step in (signed_build, approved):
        v = step(signing, repo, t, run)
        if not v.ok:
            return v
    return Verdict(True, "", "trusted")


def same_digest(inspect_output: str, digest: str) -> Verdict:
    """After `docker pull image@digest`, the local image must list exactly that digest."""
    if digest in (inspect_output or ""):
        return Verdict(True, "", "same-digest")
    return Verdict(False, "the pulled image does not carry the verified digest", "same-digest")
