"""Legal pack: every flag combination renders, nothing is invented, the draft banner and disclaimers are there."""

import itertools
from types import SimpleNamespace

import pytest

from avk import legal, stg

FULL = {"legal_name": "Acme", "postal_address": "PO Box 1", "effective_date": "2026-11-25", "data_collected": ["email"],
        "processors": ["cloudflare", "resend"], "data_location": "US", "retention": "until deleted",
        "site_url": "https://x.example", "payment_processor": "Stripe", "refund_terms": "14 days",
        "return_terms": "30 days", "governing_law": "Texas", "shipping_terms": "US only", "shipping_times": "5 days"}


def test_every_template_carries_the_not_legal_advice_disclaimer():
    for path in sorted(legal.TEMPLATE_DIR.glob("*.j2")) + sorted(legal.STATIC_DIR.iterdir()):
        assert "legal advice" in path.read_text(encoding="utf-8").lower(), path.name


@pytest.mark.parametrize("sells,subs,physical", list(itertools.product([False, True], repeat=3)))
@pytest.mark.parametrize("regions", [[], ["tx"], ["tx", "ca", "eu"]])
@pytest.mark.parametrize("analytics", [[], ["Plausible"]])
def test_every_flag_combination_renders_with_no_undefined_variable(sells, subs, physical, regions, analytics):
    r = legal.render("demo", {**FULL, "sells_goods": sells, "subscriptions": subs, "physical_goods": physical,
                              "regions": regions, "analytics": analytics})
    assert r.missing == []
    names = set(r.pages)
    assert {"terms", "privacy", "cookies", "accessibility", "contact"} <= names
    assert ("refunds" in names) == (sells or subs)
    assert ("shipping" in names) == physical
    assert all(f"{legal.MARKER}" not in text for text in r.files.values())


def test_region_sections_follow_the_flags():
    text = lambda regions: legal.render("demo", {**FULL, "regions": regions}).files["src/main/resources/static/legal/privacy.html"]
    assert "Texas Data Privacy" in text(["tx"]) and "CCPA" not in text(["tx"])
    assert "CCPA" in text(["ca"]) and "EEA" in text(["eu"])
    assert "Texas Data Privacy" not in text([])
    assert "privacy@avkaiexplorer.com" in text([])


def test_subscription_refund_page_has_auto_renewal_and_cancellation_steps():
    page = legal.render("demo", {**FULL, "subscriptions": True}).files["src/main/resources/static/legal/refunds.html"]
    assert "renew automatically" in page and "How to cancel" in page


def test_missing_inputs_render_as_markers_and_are_listed():
    r = legal.render("demo", {"legal_name": "Acme"})
    assert {"postal_address", "effective_date", "data_location", "retention"} <= set(r.missing)
    assert 'class="todo">[TO COMPLETE: postal_address]' in r.files["src/main/resources/static/legal/terms.html"]


def test_unknown_processor_is_not_invented():
    r = legal.render("demo", {**FULL, "processors": ["Acme Analytics"]})
    assert "processors.Acme Analytics" in r.missing


def test_draft_banner_stays_until_the_owner_sets_reviewed_true():
    page = lambda reviewed: legal.render("demo", {**FULL, "reviewed": reviewed}).files["src/main/resources/static/legal/terms.html"]
    assert "DRAFT – needs review by a qualified attorney/CPA" in page(False)
    assert "DRAFT" not in page(True)
    assert "DRAFT" in page("yes")                       # only a real boolean true removes it


def test_values_are_html_escaped():
    page = legal.render("demo", {**FULL, "legal_name": "A <script>x</script>"}).files["src/main/resources/static/legal/terms.html"]
    assert "<script>x" not in page and "&lt;script&gt;" in page


def test_consent_mode_follows_analytics():
    assert 'data-mode="notice"' in legal.render("demo", FULL).files["src/main/resources/static/legal/terms.html"]
    assert 'data-mode="consent"' in legal.render("demo", {**FULL, "analytics": ["x"]}).files["src/main/resources/static/legal/terms.html"]


def test_security_txt_points_at_the_security_mailbox():
    text = legal.render("demo", FULL).files["src/main/resources/static/.well-known/security.txt"]
    assert "Contact: mailto:security@avkaiexplorer.com" in text and "Expires: 2027-11-25T00:00:00Z" in text


def test_bad_region_is_rejected():
    with pytest.raises(SystemExit):
        legal.render("demo", {**FULL, "regions": ["mars"]})


def test_footer_splice_replaces_only_the_marked_region():
    page = f"<body>\n{legal.FOOTER_START}\nold\n{legal.FOOTER_END}\n</body>"
    out = legal.splice_footer(page, "<footer>new</footer>")
    assert "old" not in out and "<footer>new</footer>" in out and out.startswith("<body>")
    assert legal.splice_footer(out, "<footer>new</footer>") == out
    with pytest.raises(ValueError):
        legal.splice_footer("<body></body>", "x")


def _app(tmp_path, monkeypatch, legal_block):
    app = tmp_path / "demo"
    app.mkdir()
    import yaml
    (app / "avk.yaml").write_text(yaml.safe_dump({"name": "demo", "legal": legal_block}), encoding="utf-8")
    monkeypatch.setattr(stg, "app_dir", lambda name: app)
    monkeypatch.setattr(legal, "app_status", lambda name: "stg")
    monkeypatch.setattr(legal, "_site_url", lambda name: "https://demo.example")
    return app


def test_render_is_idempotent_and_check_detects_stale_output(tmp_path, monkeypatch, capsys):
    app = _app(tmp_path, monkeypatch, {**FULL, "footer_into": ["index.html"]})
    (app / "index.html").write_text(f"<body>{legal.FOOTER_START}{legal.FOOTER_END}</body>", encoding="utf-8")
    run = lambda check: legal.main(SimpleNamespace(app="demo", check=check))
    assert run(True) == 1                               # nothing rendered yet
    assert run(False) == 0
    assert "written" in capsys.readouterr().out
    assert run(True) == 0
    assert run(False) == 0 and "0 file(s) written" in capsys.readouterr().out
    (app / "src/main/resources/static/legal/terms.html").write_text("edited", encoding="utf-8")
    assert run(True) == 1
    assert "Privacy Policy" in (app / "index.html").read_text(encoding="utf-8")


def test_check_fails_for_a_live_app_with_markers_but_not_for_stg(tmp_path, monkeypatch):
    _app(tmp_path, monkeypatch, {"legal_name": "Acme"})
    run = lambda: (legal.main(SimpleNamespace(app="demo", check=False)), legal.main(SimpleNamespace(app="demo", check=True)))[1]
    assert run() == 0                                   # stg: markers are allowed
    monkeypatch.setattr(legal, "app_status", lambda name: "live")
    assert run() == 1


def test_missing_legal_block_is_a_clear_error(tmp_path, monkeypatch):
    app = tmp_path / "demo"
    app.mkdir()
    (app / "avk.yaml").write_text("name: demo\n", encoding="utf-8")
    monkeypatch.setattr(stg, "app_dir", lambda name: app)
    with pytest.raises(SystemExit):
        legal.load_legal("demo")


def test_support_ack_is_rendered_from_the_phase_2_template_with_placeholders():
    files = legal.render("demo", FULL).files
    assert "@@TICKET_ID@@" in files["src/main/resources/mail/support_ack.txt"]
    assert "@@FIRST_NAME@@" in files["src/main/resources/mail/support_ack.html"]
    assert "2 business days" in files["src/main/resources/mail/support_ack.txt"]
    assert files["src/main/resources/mail/support_ack.subject.txt"].strip()


def test_smtp_env_for_stg_points_at_mailpit_and_never_needs_a_key():
    env = legal.smtp_env("stg")
    assert env["SMTP_HOST"] == "mailpit" and env["SMTP_PORT"] == "1025" and env["SMTP_PASSWORD"] == ""


# --- host truststore (Norton re-signs outgoing mail TLS; Java in a container must trust the PC's roots) -------------------
from avk import truststore                                    # noqa: E402
from avk.deployer import dockerops                            # noqa: E402

PEM = "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n"


def test_truststore_imports_every_certificate_and_counts_failures(tmp_path):
    pem = tmp_path / "roots.pem"
    pem.write_text(PEM * 3, encoding="ascii")
    calls = []

    def fake(argv):
        calls.append(argv)
        if len(calls) == 2:
            return 1                                          # one certificate keytool refuses
        open(argv[argv.index("-keystore") + 1], "wb").write(b"p12")
        return 0

    out = tmp_path / "certs" / "host-truststore.p12"
    assert truststore.build(pem, out, keytool="keytool", run=fake) == (2, 1)
    assert out.read_bytes() == b"p12" and not out.with_suffix(".tmp").exists()
    assert all("changeit" in c for c in calls)


def test_truststore_fails_clearly_when_nothing_imports_or_pem_is_empty(tmp_path):
    pem = tmp_path / "roots.pem"
    pem.write_text(PEM, encoding="ascii")
    with pytest.raises(SystemExit):
        truststore.build(pem, tmp_path / "x.p12", keytool="keytool", run=lambda argv: 1)
    pem.write_text("nothing", encoding="ascii")
    with pytest.raises(SystemExit):
        truststore.build(pem, tmp_path / "x.p12", keytool="keytool", run=lambda argv: 0)


def test_start_app_mounts_the_truststore_read_only_only_when_asked(tmp_path):
    seen = []
    docker = dockerops.Docker(run=lambda argv, stdin=None: (seen.append(argv), (0, "", ""))[1])
    docker.start_app("hello-blue", "img@sha256:x", tmp_path / "e.env", {"avk.app": "hello"})
    plain = seen[-1]
    assert "-v" not in plain and not any(a.startswith("JAVA_TOOL_OPTIONS") for a in plain)
    store = tmp_path / "host-truststore.p12"
    docker.start_app("hello-blue", "img@sha256:x", tmp_path / "e.env", {"avk.app": "hello"}, store)
    argv = seen[-1]
    assert f"{store.as_posix()}:/certs/host-truststore.p12:ro" in argv
    assert any(a.startswith("JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStore=/certs/host-truststore.p12") for a in argv)
    assert "--read-only" in argv and "--cap-drop" in argv     # hardening unchanged


def test_deployer_uses_the_truststore_only_for_apps_that_ask_and_only_if_the_file_exists(tmp_path, monkeypatch):
    from avk import config
    from avk.deployer import bluegreen
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    deployer = bluegreen.Deployer.__new__(bluegreen.Deployer)
    logged = []
    deployer.log = lambda event, **kw: logged.append(event)
    assert deployer.host_truststore({"port": 1}) is None
    assert deployer.host_truststore({"host_truststore": True}) is None and logged == ["truststore_missing"]
    (tmp_path / "certs").mkdir()
    (tmp_path / "certs" / "host-truststore.p12").write_bytes(b"x")
    assert deployer.host_truststore({"host_truststore": True}) == config.avk_home() / "certs" / "host-truststore.p12"
    assert deployer.host_truststore({"host_truststore": "yes"}) is None            # only a real boolean opts in


# --- drill findings: a new machine has no certs file, and the login database restores differently ----------------------------
def test_a_missing_host_roots_file_is_created_before_docker_can_make_a_folder_of_it(tmp_path):
    made = []

    def export(pem):
        pem.write_text("PEM", encoding="ascii")
        made.append(pem)
        return 1

    assert truststore.ensure_host_roots(tmp_path / "certs", export=export) is None and made
    assert truststore.ensure_host_roots(tmp_path / "certs", export=lambda p: 1 / 0) is None      # already there: not exported again


def test_a_folder_that_docker_created_in_its_place_is_replaced(tmp_path):
    (tmp_path / "host-roots.pem").mkdir()
    assert truststore.ensure_host_roots(tmp_path, export=lambda p: (p.write_text("x"), 1)[1]) is None
    assert (tmp_path / "host-roots.pem").is_file()


def test_export_failure_gives_a_clear_message_not_a_crash(tmp_path):
    def boom(pem):
        raise OSError("no store")

    assert "avk certs refresh" in truststore.ensure_host_roots(tmp_path, export=boom)


# --- sites without server code (the static template): the contact page is a mailto link, not a form -----------------------------
def test_without_a_contact_form_there_is_no_form_and_no_acknowledgement_mail():
    r = legal.render("demo", {**FULL, "contact_form": False, "accounts": False})
    page = r.files["src/main/resources/static/legal/contact.html"]
    assert "<form" not in page and "mailto:support@avkaiexplorer.com" in page and "ticket number" not in page
    assert not [f for f in r.files if "support_ack" in f]
    assert r.missing == []


def test_with_the_form_the_page_and_the_ack_mail_are_unchanged():
    r = legal.render("demo", FULL)
    assert "<form" in r.files["src/main/resources/static/legal/contact.html"]
    assert any("support_ack" in f for f in r.files)
