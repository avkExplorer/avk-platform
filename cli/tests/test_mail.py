from avk import mail

GOOD = {
    ("example.com", "MX"): ["22 route3.mx.cloudflare.net"],
    ("example.com", "TXT"): ["v=spf1 include:_spf.mx.cloudflare.net ~all"],
    ("_spf.mx.cloudflare.net", "TXT"): ["v=spf1 ip4:1.2.3.4 ~all"],
    ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=none; rua=mailto:dmarc@example.com"],
    ("resend._domainkey.notify.example.com", "TXT"): ["p=MIGfMA0"],
    ("send.notify.example.com", "CNAME"): ["send.forge.rmta.net"],
    ("rsend.notify.example.com", "CNAME"): ["rsend.forge.rmta.net"],
}


def fake(records):
    return lambda name, rrtype: records.get((name, rrtype), [])


def statuses(records):
    return {name: ok for name, ok, _ in mail.run_checks("example.com", fake(records))}


def test_all_good():
    assert all(statuses(GOOD).values())


def test_two_spf_records_fail():
    bad = {**GOOD, ("example.com", "TXT"): ["v=spf1 ~all", "v=spf1 -all"]}
    assert statuses(bad)["example.com SPF"] is False


def test_spf_without_all_fails():
    bad = {**GOOD, ("example.com", "TXT"): ["v=spf1 include:_spf.mx.cloudflare.net"]}
    assert statuses(bad)["example.com SPF"] is False


def test_spf_lookup_limit_counted_through_includes():
    many = " ".join(f"include:i{n}.example.net" for n in range(11))
    bad = {**GOOD, ("example.com", "TXT"): [f"v=spf1 {many} ~all"]}
    assert statuses(bad)["example.com SPF"] is False
    assert mail.count_spf_lookups(f"v=spf1 {many} ~all", fake({})) == 11
    nested = {**GOOD, ("_spf.mx.cloudflare.net", "TXT"): ["v=spf1 a mx ~all"]}
    assert mail.count_spf_lookups("v=spf1 include:_spf.mx.cloudflare.net ~all", fake(nested)) == 3


def test_missing_dmarc_and_mx_fail():
    bad = {k: v for k, v in GOOD.items() if k[0] not in ("_dmarc.example.com", "example.com") or k[1] == "TXT"}
    s = statuses(bad)
    assert s["_dmarc.example.com"] is True  # still present in this variant
    bad = {k: v for k, v in GOOD.items() if k != ("_dmarc.example.com", "TXT") and k != ("example.com", "MX")}
    s = statuses(bad)
    assert s["_dmarc.example.com"] is False and s["example.com MX"] is False


def test_test_send_refuses_without_yes(capsys):
    from types import SimpleNamespace

    assert mail.test_send(SimpleNamespace(to="a@example.com", yes=False)) == 1
    assert "--yes" in capsys.readouterr().out


def test_send_request_keeps_key_in_header_only():
    import json

    env = {"RESEND_API_KEY": "re_SECRETKEY1234567890", "MAIL_FROM_APPS": "noreply@notify.example.com"}
    req = mail.build_send_request(env, "x@example.com", {"subject": "s", "html": "<p>h</p>", "text": "t"})
    assert req.get_header("Authorization") == "Bearer re_SECRETKEY1234567890"
    assert b"re_SECRET" not in req.data and req.full_url.startswith("https://api.resend.com/")
    assert json.loads(req.data)["to"] == ["x@example.com"]


def test_missing_resend_records_fail():
    bad = {k: v for k, v in GOOD.items() if "notify" not in k[0]}
    s = statuses(bad)
    assert not any(ok for name, ok in s.items() if "notify" in name)
