"""Render the transactional email templates in email/templates/ (Jinja2, strict: a missing variable is an error).

Templates are transactional only: no marketing content. The postal address is a slot (`postal_address`): use a PO
box or business address, never a home address.
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from avk import config

TEMPLATE_DIR = config.REPO_ROOT / "email" / "templates"
NAMES = ("welcome", "verify_email", "password_reset", "order_receipt", "refund_confirmation", "support_ack")


def environment() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), undefined=StrictUndefined,
                       autoescape=select_autoescape(["html.j2"], default=False), keep_trailing_newline=True)


def render(name: str, context: dict) -> dict[str, str]:
    """Returns subject, html and text for one template."""
    env = environment()
    return {
        "subject": env.get_template(f"{name}.subject.j2").render(**context).strip(),
        "html": env.get_template(f"{name}.html.j2").render(**context),
        "text": env.get_template(f"{name}.txt.j2").render(**context).strip() + "\n",
    }
