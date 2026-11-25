from types import SimpleNamespace

from avk import config, stg


def test_index_lists_apps_and_escapes_names():
    page = stg.index_html([{"name": "hello", "stg_port": 4820}, {"name": "<b>x</b>", "stg_port": 4821}])
    assert 'href="http://127.0.0.1:4820/"' in page and "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page
    assert "Mailpit" in page


def test_app_container_is_hardened_and_bound_to_loopback_only(tmp_path):
    argv = stg.run_args("hello", 4820, 8080, tmp_path / "hello.env", "avk-stg/hello:local")
    joined = " ".join(argv)
    assert "--read-only" in argv and "--cap-drop ALL" in joined and "no-new-privileges:true" in joined
    assert "127.0.0.1:4820:8080" in argv
    assert "--network avk-stg" in joined and "avk-prod" not in joined
    assert "docker.sock" not in joined and "-v " not in joined and "--volume" not in joined   # no socket, no host folder
    assert "--privileged" not in argv


def test_stg_stack_is_isolated_from_prod_and_bound_to_loopback():
    raw = (config.REPO_ROOT / "runtime" / "compose.stg.yaml").read_text(encoding="utf-8")
    text = "\n".join(ln.split("#", 1)[0] for ln in raw.splitlines())      # ignore comments
    assert "name: avk-stg" in text and "avk-prod" not in text and "pg-prod" not in text
    ports = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- \"") and ":" in ln]
    assert ports and all(p.startswith('- "127.0.0.1:') for p in ports)
    images = [ln.split("image:", 1)[1].split()[0] for ln in text.splitlines() if ln.strip().startswith("image:")]
    # Every image is pinned by digest, except the locally built `avk/keycloak`, whose base is pinned in its Dockerfile.
    assert images and all("@sha256:" in i or i.startswith("avk/keycloak:") for i in images)
    dockerfile = (config.REPO_ROOT / "runtime" / "keycloak" / "Dockerfile").read_text(encoding="utf-8")
    assert any(ln.startswith("FROM ") and "@sha256:" in ln for ln in dockerfile.splitlines())


def test_prod_compose_publishes_nothing_but_loopback_and_pins_images():
    text = (config.REPO_ROOT / "runtime" / "compose.prod.yaml").read_text(encoding="utf-8")
    ports = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- \"") and ":" in ln]
    assert all(p.startswith('- "127.0.0.1:') for p in ports)
    assert "5432:" not in text                      # pg-prod has no host port
    assert "docker.sock" not in text


def test_seed_refuses_prod(capsys):
    assert stg.seed(SimpleNamespace(app="hello", env="prod")) == 1
    assert "only ever writes to stg" in capsys.readouterr().out


def test_stg_down_uses_label_filter_not_name_guessing():
    import inspect

    assert "label=avk.env=stg" in inspect.getsource(stg.main)
