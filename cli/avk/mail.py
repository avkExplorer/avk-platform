"""`avk mail check`: validate the DNS side of business email for the root domain and notify.<domain>.

Checks (all read-only DNS queries against a public resolver):
  root:    MX present; exactly one SPF record, under 10 DNS lookups, ending in ~all or -all; DMARC present with a policy.
  notify:  the Resend DKIM key and the two Resend bounce/sending CNAMEs. (Resend publishes its SPF on the `send.`
           host through the CNAME, so notify itself has no SPF TXT of its own.)
DMARC is inherited by notify from the root, so only the root needs one.
"""

from __future__ import annotations

import re
from typing import Callable

from avk import config

# Resolver: (name, rrtype) -> list of text values ("" for no answer). TXT strings are joined.
Resolve = Callable[[str, str], list[str]]

SPF_LOOKUP_MECHANISMS = ("include:", "a", "mx", "ptr", "exists:", "redirect=")
RESEND_HOSTS = ("send", "rsend")
DKIM_SELECTOR = "resend._domainkey"


def default_resolver() -> Resolve:
    import dns.exception
    import dns.resolver

    res = dns.resolver.Resolver(configure=False)
    res.nameservers = ["1.1.1.1", "8.8.8.8"]
    res.lifetime = 8.0

    def resolve(name: str, rrtype: str) -> list[str]:
        try:
            answer = res.resolve(name, rrtype)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.DNSException as exc:  # timeout etc.: surface as a failed check, not a crash
            raise RuntimeError(f"DNS query {rrtype} {name} failed: {exc}") from exc
        out = []
        for rdata in answer:
            if rrtype == "TXT":
                out.append("".join(s.decode("utf-8", "replace") for s in rdata.strings))
            elif rrtype == "MX":
                out.append(f"{rdata.preference} {str(rdata.exchange).rstrip('.')}")
            else:
                out.append(str(rdata.target if hasattr(rdata, "target") else rdata).rstrip("."))
        return out

    return resolve


def spf_records(txts: list[str]) -> list[str]:
    return [t for t in txts if t.lower().startswith("v=spf1")]


def count_spf_lookups(spf: str, resolve: Resolve, depth: int = 0, seen: set[str] | None = None) -> int:
    """DNS-lookup-causing terms in an SPF record, following includes (RFC 7208 limit: 10)."""
    seen = seen if seen is not None else set()
    if depth > 6:
        return 0
    total = 0
    for term in spf.split()[1:]:
        term = term.lstrip("+-~?")
        low = term.lower()
        if low.startswith("include:"):
            total += 1
            target = term.split(":", 1)[1]
            if target.lower() not in seen:
                seen.add(target.lower())
                inner = spf_records(resolve(target, "TXT"))
                if inner:
                    total += count_spf_lookups(inner[0], resolve, depth + 1, seen)
        elif low.startswith("redirect="):
            total += 1
        elif low in ("a", "mx", "ptr") or re.match(r"^(a|mx|ptr|exists)[:/]", low):
            total += 1
    return total


def check_root(domain: str, resolve: Resolve) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    mx = resolve(domain, "MX")
    results.append((f"{domain} MX", bool(mx), ", ".join(mx) if mx else "no MX records: inbound mail has nowhere to go"))

    spf = spf_records(resolve(domain, "TXT"))
    if len(spf) != 1:
        results.append((f"{domain} SPF", False, f"expected exactly one SPF record, found {len(spf)}"))
    else:
        lookups = count_spf_lookups(spf[0], resolve)
        ends_ok = spf[0].rstrip().endswith(("~all", "-all"))
        ok = lookups <= 10 and ends_ok
        why = f"{lookups} lookup(s)" + ("" if ends_ok else "; should end in ~all or -all") + ("" if lookups <= 10 else "; over the limit of 10")
        results.append((f"{domain} SPF", ok, why))

    dmarc = [t for t in resolve(f"_dmarc.{domain}", "TXT") if t.upper().startswith("V=DMARC1")]
    if len(dmarc) != 1:
        results.append((f"_dmarc.{domain}", False, f"expected one DMARC record, found {len(dmarc)}"))
    else:
        policy = re.search(r"\bp=(none|quarantine|reject)\b", dmarc[0])
        results.append((f"_dmarc.{domain}", bool(policy), f"policy {policy.group(1)}" if policy else "no valid p= policy"))
    return results


def check_notify(domain: str, resolve: Resolve) -> list[tuple[str, bool, str]]:
    host = f"notify.{domain}"
    results: list[tuple[str, bool, str]] = []
    dkim = [t for t in resolve(f"{DKIM_SELECTOR}.{host}", "TXT") if "p=" in t]
    results.append((f"{DKIM_SELECTOR}.{host} DKIM", bool(dkim), "public key present" if dkim else "missing: verify the domain in Resend"))
    for label in RESEND_HOSTS:
        cname = resolve(f"{label}.{host}", "CNAME")
        results.append((f"{label}.{host} CNAME", bool(cname), cname[0] if cname else "missing"))
    return results


def run_checks(domain: str, resolve: Resolve) -> list[tuple[str, bool, str]]:
    return check_root(domain, resolve) + check_notify(domain, resolve)


RESEND_URL = "https://api.resend.com/emails"


def load_mail_env() -> dict[str, str]:
    path = config.avk_home() / "secrets" / "prod" / "platform-mail.env"
    if not path.is_file():
        raise SystemExit(f"fail  {path} not found: run tools\\set-mail-secret.ps1")
    from avk import tf  # same simple KEY=value parser as the Cloudflare secrets

    values = tf.parse_env_file(path.read_text(encoding="utf-8"))
    if not values.get("RESEND_API_KEY", "").startswith("re_"):
        raise SystemExit("fail  RESEND_API_KEY missing or malformed in platform-mail.env")
    return values


def build_send_request(env: dict[str, str], to: str, rendered: dict[str, str]):
    """Resend API request for one message. The key goes only in the Authorization header."""
    import json
    import urllib.request

    body = json.dumps({"from": f"AVK Explorer <{env.get('MAIL_FROM_APPS', 'noreply@notify.avkaiexplorer.com')}>",
                       "to": [to], "subject": rendered["subject"], "html": rendered["html"], "text": rendered["text"]}).encode()
    return urllib.request.Request(RESEND_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {env['RESEND_API_KEY']}", "Content-Type": "application/json", "User-Agent": "avk-mail/0.1"})


def test_send(args) -> int:
    """Sends ONE real test message (the welcome template with sample data). Requires --yes."""
    import urllib.error
    import urllib.request

    from avk import mailtpl

    if not args.yes:
        print(f"fail  this sends a real email to {args.to}: re-run with --yes after approving the recipient")
        return 1
    ctx = {"brand_name": "AVK Explorer", "support_email": "support@avkaiexplorer.com",
           "postal_address": "", "first_name": "there", "login_url": "https://avkaiexplorer.com/", "subject": "Test"}
    rendered = mailtpl.render("welcome", ctx)
    rendered["subject"] = "AVK mail test: " + rendered["subject"]
    try:
        with urllib.request.urlopen(build_send_request(load_mail_env(), args.to, rendered), timeout=30) as resp:
            print(f"ok    sent (HTTP {resp.status}) to {args.to}: {resp.read().decode()[:120]}")
        return 0
    except urllib.error.HTTPError as exc:
        print(f"fail  Resend refused it (HTTP {exc.code}): {exc.read().decode()[:300]}")
        return 1


def main(args) -> int:
    platform = config.load()
    try:
        results = run_checks(platform.domain, default_resolver())
    except RuntimeError as exc:
        print(f"fail  {exc}")
        return 1
    bad = 0
    for name, ok, detail in results:
        print(f"{'ok  ' if ok else 'fail'}  {name}: {detail}")
        bad += 0 if ok else 1
    return 1 if bad else 0
