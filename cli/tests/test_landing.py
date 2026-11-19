from avk import landing


def test_site_has_both_pages():
    assert (landing.SITE_DIR / "index.html").is_file()
    assert (landing.SITE_DIR / "status" / "index.html").is_file()


def test_docker_argv_never_contains_the_token(monkeypatch):
    monkeypatch.setenv("AVK_HOME", "C:/avk")
    argv = landing.docker_argv()
    assert "CLOUDFLARE_API_TOKEN" in argv          # passed by name only
    assert not any("=" in a and a.startswith("CLOUDFLARE_API_TOKEN") for a in argv)
    assert landing.PROJECT in argv
