import json

from avk import config, prod


def test_slash():
    assert prod.slash(r"C:\avk\state") == "C:/avk/state"


def test_test_route_yaml_routes_each_host():
    text = prod.test_route_yaml(["a.avkaiexplorer.com", "b.avkaiexplorer.com"])
    assert "Host(`a.avkaiexplorer.com`)" in text and "Host(`b.avkaiexplorer.com`)" in text
    assert "http://whoami:80" in text


def test_prepare_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    assert "tunnel.token" in prod.prepare()


def test_prepare_writes_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    token = tmp_path / "secrets" / "platform" / "tunnel.token"
    token.parent.mkdir(parents=True)
    token.write_text("x")
    assert prod.prepare() is None
    route = (tmp_path / "state" / "traefik" / "phase1-test.yml").read_text(encoding="utf-8")
    assert "hello.avkaiexplorer.com" in route


def test_status_rows_parses_compose_json():
    out = "\n".join(json.dumps(c) for c in [{"Service": "traefik", "Health": "healthy"},
                                            {"Service": "whoami", "Health": "", "State": "running"}])
    assert prod.status_rows(out) == [("traefik", "healthy"), ("whoami", "running")]


def test_compose_file_pins_every_image_by_digest():
    text = (config.REPO_ROOT / "runtime" / "compose.prod.yaml").read_text(encoding="utf-8")
    images = [ln.split("image:", 1)[1].split()[0] for ln in text.splitlines() if ln.strip().startswith("image:")]
    assert images and all("@sha256:" in i for i in images)
