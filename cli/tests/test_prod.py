import json

from avk import config, prod


def test_slash():
    assert prod.slash(r"C:\avk\state") == "C:/avk/state"


def test_prepare_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    assert "tunnel.token" in prod.prepare()


class FakeRoutes:
    """Stands in for the Docker-volume route writer: tests must never reach Docker."""
    written: dict = {}

    def __init__(self, *a, **k):
        pass

    def ensure_volume(self):
        pass

    def write(self, name, text):
        FakeRoutes.written[name] = text

    def restore(self, name, snapshot):
        FakeRoutes.removed.append(name)


FakeRoutes.removed = []


def test_prepare_writes_no_placeholder_route_and_clears_the_old_one(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    monkeypatch.setattr("avk.deployer.traefik.VolumeRoutes", FakeRoutes)
    FakeRoutes.written = {}
    FakeRoutes.removed = []
    token = tmp_path / "secrets" / "platform" / "tunnel.token"
    token.parent.mkdir(parents=True)
    token.write_text("x")
    assert prod.prepare() is None
    assert FakeRoutes.written == {}                 # real routes belong to the deployer
    assert FakeRoutes.removed == ["phase1-test"]    # a placeholder left by an older run is removed


def test_status_rows_parses_compose_json():
    out = "\n".join(json.dumps(c) for c in [{"Service": "traefik", "Health": "healthy"},
                                            {"Service": "pg-prod", "Health": "", "State": "running"}])
    assert prod.status_rows(out) == [("traefik", "healthy"), ("pg-prod", "running")]


def test_compose_file_pins_every_image_by_digest():
    text = (config.REPO_ROOT / "runtime" / "compose.prod.yaml").read_text(encoding="utf-8")
    images = [ln.split("image:", 1)[1].split()[0] for ln in text.splitlines() if ln.strip().startswith("image:")]
    assert images and all("@sha256:" in i for i in images)
