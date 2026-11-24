import pytest
from jinja2 import UndefinedError

from avk import mailtpl

BASE = {"brand_name": "Acme", "support_email": "support@example.com", "postal_address": "PO Box 1, Town", "first_name": "Sam"}
EXTRA = {
    "welcome": {"login_url": "https://x.example.com/login"},
    "verify_email": {"verify_url": "https://x.example.com/v?t=1", "expires_in": "24 hours"},
    "password_reset": {"reset_url": "https://x.example.com/r?t=1", "expires_in": "1 hour"},
    "order_receipt": {"order_number": "A-100", "total": "$12.00",
                      "items": [{"name": "Widget", "quantity": 2, "total": "$12.00"}]},
    "refund_confirmation": {"order_number": "A-100", "refund_amount": "$12.00", "days": 5},
    "support_ack": {"ticket_id": "T-9", "response_time": "2 business days"},
}


@pytest.mark.parametrize("name", mailtpl.NAMES)
def test_each_template_renders_html_and_text(name):
    out = mailtpl.render(name, {**BASE, **EXTRA[name], "subject": "s"})
    assert out["subject"] and "{{" not in out["subject"]
    for part in ("html", "text"):
        assert "{{" not in out[part] and "{%" not in out[part]
        assert "Acme" in out[part] and "support@example.com" in out[part] and "PO Box 1" in out[part]
    assert "<html" in out["html"]


def test_html_is_escaped():
    out = mailtpl.render("welcome", {**BASE, **EXTRA["welcome"], "first_name": "<script>x</script>", "subject": "s"})
    assert "<script>" not in out["html"] and "&lt;script&gt;" in out["html"]


def test_missing_variable_is_an_error():
    ctx = {**BASE, **EXTRA["welcome"], "subject": "s"}
    del ctx["login_url"]
    with pytest.raises(UndefinedError):
        mailtpl.render("welcome", ctx)


def test_no_marketing_wording():
    for name in mailtpl.NAMES:
        text = mailtpl.render(name, {**BASE, **EXTRA[name], "subject": "s"})["text"].lower()
        assert not any(w in text for w in ("sale", "discount", "% off", "unsubscribe from"))
