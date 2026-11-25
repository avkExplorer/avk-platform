"""`avk legal render <app>`: policy pages for an app from the shared templates in legal/templates/.

Inputs come from the `legal:` block of the app's avk.yaml. Every page starts with a DRAFT banner until the owner sets
`legal.reviewed: true`. Templates never invent facts: a missing input renders as a highlighted [TO COMPLETE: name]
marker and is listed. This is a drafting aid, not legal advice.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from avk import auth, config, mailtpl, secretgen, stg

TEMPLATE_DIR = config.REPO_ROOT / "legal" / "templates"
STATIC_DIR = config.REPO_ROOT / "legal" / "static"
MARKER = "[TO COMPLETE"
FOOTER_START = "<!-- avk-legal-footer:start -->"
FOOTER_END = "<!-- avk-legal-footer:end -->"
REGIONS = {"tx", "ca", "eu"}

# (slug, title, applies(flags)). Terms, privacy, cookies, accessibility and contact are always rendered.
PAGES = [
    ("terms", "Terms of Service", lambda s: True),
    ("privacy", "Privacy Policy", lambda s: True),
    ("cookies", "Cookie Notice", lambda s: True),
    ("refunds", "Refunds, Returns and Cancellation", lambda s: s["sells_goods"] or s["subscriptions"]),
    ("shipping", "Shipping", lambda s: s["physical_goods"]),
    ("accessibility", "Accessibility Statement", lambda s: True),
    ("contact", "Contact", lambda s: True),
]

# Providers we know. Unknown names still render, with a [TO COMPLETE] purpose, so nothing is invented.
PROCESSORS = {
    "cloudflare": ("Cloudflare", "network delivery, security filtering and DNS", "United States and worldwide"),
    "resend": ("Resend", "sending our emails (verification, receipts, replies)", "United States"),
    "stripe": ("Stripe", "taking and refunding payments", "United States"),
    "turnstile": ("Cloudflare Turnstile", "checking that a form is filled in by a person", "United States and worldwide"),
}

DEFAULTS = {
    "support_email": "support@avkaiexplorer.com",
    "privacy_email": "privacy@avkaiexplorer.com",
    "security_email": "security@avkaiexplorer.com",
    "response_time": "2 business days",
    "base_path": "/legal",
    "output_dir": "src/main/resources/static/legal",
    "well_known_dir": "src/main/resources/static/.well-known",
    "mail_dir": "src/main/resources/mail",
}

# The app fills these in when it sends the acknowledgement (Phase 2 support_ack template).
ACK_PLACEHOLDERS = {"first_name": "@@FIRST_NAME@@", "ticket_id": "@@TICKET_ID@@"}


@dataclass
class Rendered:
    files: dict[str, str] = field(default_factory=dict)     # path relative to the app folder -> text
    missing: list[str] = field(default_factory=list)        # names of inputs still to complete
    pages: list[str] = field(default_factory=list)
    footer: str = ""                                        # the footer fragment for hand-written pages


def _todo(name: str) -> Markup:
    return Markup(f'<mark class="todo">{MARKER}: {name}]</mark>')


def _flags(legal: dict) -> dict:
    regions = {str(r).lower() for r in legal.get("regions") or []}
    unknown = regions - REGIONS
    if unknown:
        raise SystemExit(f"fail  legal.regions: unknown {sorted(unknown)}; allowed {sorted(REGIONS)}")
    return {
        "sells_goods": bool(legal.get("sells_goods")), "subscriptions": bool(legal.get("subscriptions")),
        "physical_goods": bool(legal.get("physical_goods")), "accounts": bool(legal.get("accounts", True)),
        "tx": "tx" in regions, "ca": "ca" in regions, "eu": "eu" in regions,
    }


def _processors(legal: dict, missing: set[str]) -> list[dict]:
    rows = []
    for raw in legal.get("processors") or []:
        known = PROCESSORS.get(str(raw).lower())
        if known:
            rows.append({"name": known[0], "purpose": known[1], "location": known[2]})
        else:
            missing.add(f"processors.{raw}")
            rows.append({"name": str(raw), "purpose": _todo(f"what {raw} does"), "location": _todo(f"where {raw} is")})
    return rows


def _expires(effective: str | None) -> str | None:
    try:
        d = dt.date.fromisoformat(str(effective))
    except ValueError:
        return None
    return f"{d.replace(year=d.year + 1) if not (d.month == 2 and d.day == 29) else d.replace(year=d.year + 1, day=28)}T00:00:00Z"


def _site_url(app: str) -> str | None:
    try:
        for a in config.load().apps:
            if a["name"] == app and a.get("hosts"):
                return f"https://{a['hosts'][0]}"
    except Exception:  # noqa: BLE001 - the manifest may be absent in unit tests
        return None
    return None


def app_status(app: str) -> str:
    try:
        for a in config.load().apps:
            if a["name"] == app:
                return a.get("status", "stg")
    except Exception:  # noqa: BLE001
        pass
    return "stg"


def render(app: str, legal: dict, site_url: str | None = None) -> Rendered:
    """Render every applicable page, the static assets and security.txt for one app."""
    values = {**DEFAULTS, **{k: v for k, v in legal.items() if v not in (None, "")}}
    values.setdefault("app_title", app)
    if site_url and "site_url" not in values:
        values["site_url"] = site_url
    if values.get("regions") and "tx" in [str(r).lower() for r in values["regions"]]:
        values.setdefault("governing_law", "the laws of the State of Texas, USA")
    missing: set[str] = set()

    def f(key: str):
        value = values.get(key)
        if value in (None, "", []):
            missing.add(key)
            return _todo(key)
        return value

    flags = _flags(legal)
    analytics = [str(a) for a in legal.get("analytics") or []]
    base_path = str(values["base_path"]).rstrip("/")
    pages = [{"slug": s, "title": t, "href": f"{base_path}/{s}.html"} for s, t, applies in PAGES if applies(flags)]
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), undefined=StrictUndefined,
                      autoescape=select_autoescape(["html.j2"], default=False), keep_trailing_newline=True)
    expires = _expires(values.get("effective_date"))
    context = {
        **flags, "f": f, "pages": pages, "base_path": base_path, "analytics": analytics,
        "consent_mode": "consent" if analytics else "notice", "reviewed": legal.get("reviewed") is True,
        "data_collected": [str(x) for x in legal.get("data_collected") or []],
        "processors": _processors(legal, missing), "turnstile_site_key": legal.get("turnstile_site_key") or "",
        "cancellation_steps": legal.get("cancellation_steps") or "", "contact_form": legal.get("contact_form", True) is not False,
        "security_expires": expires or _todo("effective_date"),
    }
    if expires is None:
        missing.add("effective_date")

    out = Rendered()
    folder = str(values["output_dir"]).strip("/")
    for page in pages:
        out.files[f"{folder}/{page['slug']}.html"] = env.get_template(f"{page['slug']}.html.j2").render(**context, title=page["title"])
        out.pages.append(page["slug"])
    out.files[f"{folder}/legal.css"] = (STATIC_DIR / "legal.css").read_text(encoding="utf-8")
    out.files[f"{folder}/consent.js"] = (STATIC_DIR / "consent.js").read_text(encoding="utf-8")
    out.files[f"{str(values['well_known_dir']).strip('/')}/security.txt"] = env.get_template("security.txt.j2").render(**context)
    if context["contact_form"]:                         # a site with no server code (static) has no form to acknowledge
        ack = mailtpl.render("support_ack", {
            "brand_name": values["app_title"], "support_email": values["support_email"], "postal_address": legal.get("postal_address") or "",
            "response_time": values["response_time"], "subject": "Support", "login_url": "", **ACK_PLACEHOLDERS})
        mail_dir = str(values["mail_dir"]).strip("/")
        out.files[f"{mail_dir}/support_ack.subject.txt"] = ack["subject"] + "\n"
        out.files[f"{mail_dir}/support_ack.txt"] = ack["text"]
        out.files[f"{mail_dir}/support_ack.html"] = ack["html"]
    out.footer = env.get_template("_footer.html.j2").render(**context)
    out.missing = sorted(missing)
    return out


def splice_footer(text: str, footer: str) -> str:
    """Put the generated footer between the two marker comments of a hand-written page."""
    pattern = re.compile(re.escape(FOOTER_START) + r".*?" + re.escape(FOOTER_END), re.S)
    if not pattern.search(text):
        raise ValueError(f"missing {FOOTER_START} ... {FOOTER_END}")
    return pattern.sub(lambda _m: f"{FOOTER_START}\n{footer.strip()}\n{FOOTER_END}", text, count=1)


def load_legal(app: str) -> dict:
    path = stg.app_dir(app) / "avk.yaml"
    if not path.is_file():
        raise SystemExit(f"fail  {path} not found")
    legal = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("legal")
    if not isinstance(legal, dict):
        raise SystemExit(f"fail  {path} has no `legal:` block (see legal/README.md)")
    return legal


def _read(path: Path) -> str | None:
    return path.read_bytes().decode("utf-8") if path.is_file() else None


def plan(app: str, legal: dict, status: str, site_url: str | None = None) -> tuple[Rendered, dict[str, str], list[str]]:
    """Everything to write (path -> text), plus reasons the result is not acceptable."""
    base = stg.app_dir(app)
    rendered = render(app, legal, site_url)
    writes = dict(rendered.files)
    problems: list[str] = []
    for rel in legal.get("footer_into") or []:
        current = _read(base / rel)
        if current is None:
            problems.append(f"footer_into file not found: {rel}")
            continue
        try:
            writes[rel] = splice_footer(current, rendered.footer)
        except ValueError as exc:
            problems.append(f"{rel}: {exc}")
    return rendered, writes, problems


def main(args) -> int:
    app = args.app
    legal = load_legal(app)
    status = app_status(app)
    rendered, writes, problems = plan(app, legal, status, _site_url(app))
    base = stg.app_dir(app)
    stale = [rel for rel, text in writes.items() if _read(base / rel) != text]
    if args.check:
        if stale:
            problems.append("stale (run `avk legal render " + app + "`): " + ", ".join(sorted(stale)))
        if rendered.missing and status == "live":
            problems.append("app is live but these inputs are still [TO COMPLETE]: " + ", ".join(rendered.missing))
        for p in problems:
            print(f"fail  {p}")
        if problems:
            return 1
        print(f"ok    {app}: {len(writes)} files up to date" + (f"; to complete: {', '.join(rendered.missing)}" if rendered.missing else ""))
        return 0
    if problems:
        for p in problems:
            print(f"fail  {p}")
        return 1
    for rel in sorted(stale):
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(writes[rel])
    print(f"ok    {app}: pages {', '.join(rendered.pages)}; {len(stale)} file(s) written, {len(writes) - len(stale)} unchanged")
    if rendered.missing:
        print("todo  still [TO COMPLETE]: " + ", ".join(rendered.missing))
    if legal.get("reviewed") is not True:
        print("note  pages carry the DRAFT banner (legal.reviewed is not true)")
    return 0


SMTP_KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_SSL", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM", "SUPPORT_TO")


def smtp_env(env: str) -> dict[str, str]:
    """Mail settings for an app's contact form: stg goes to Mailpit, prod uses Resend (same key as Phase 2 mail)."""
    s = auth.smtp_settings(env)
    return {"SMTP_HOST": s["host"], "SMTP_PORT": s["port"], "SMTP_SSL": s["ssl"], "SMTP_USERNAME": s.get("user", ""),
            "SMTP_PASSWORD": s.get("password", ""), "MAIL_FROM": s["from"], "SUPPORT_TO": DEFAULTS["support_email"]}


def smtp(args) -> int:
    """`avk legal smtp <app> --env stg|prod`: add the mail settings to the app's runtime secrets file (values never printed)."""
    path = secretgen.app_files(args.app, args.env)["runtime"]
    if not path.is_file():
        print(f"fail  {path} missing: run `avk secrets init {args.app} --env {args.env}` first")
        return 1
    existing = secretgen.parse_env(path.read_text(encoding="utf-8"))
    merged = {**existing, **smtp_env(args.env)}
    if merged != existing:
        secretgen.write_private(path, "".join(f"{k}={v}\n" for k, v in merged.items()))
    print(f"ok    {args.app} ({args.env}): mail settings {', '.join(SMTP_KEYS)} in {path.name} (not printed)")
    return 0
