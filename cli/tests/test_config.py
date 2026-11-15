import pytest

from avk import config


def platform(apps, domain="avkaiexplorer.com", owner="someone"):
    return config.Platform(domain=domain, github_owner=owner, apps=apps)


def app(**over):
    base = {"name": "shop", "type": "web", "stg_port": 4820, "hosts": ["shop.avkaiexplorer.com"], "status": "stg"}
    base.update(over)
    return base


@pytest.mark.parametrize("name", ["shop", "my-app2", "ab", "a" * 30])
def test_good_names(name):
    assert config.name_errors(name) == []


@pytest.mark.parametrize("name", ["Shop", "a", "a" * 31, "2shop", "shop_x", "shop-", "my--app", "api-shop", "lab-shop",
                                  "www", "auth", "stg", "notify", "status", "admin", "mail", "api"])
def test_bad_names(name):
    assert config.name_errors(name)


def test_next_free_port_skips_used():
    assert config.next_free_port(set()) == 4820
    assert config.next_free_port({4820, 4821}) == 4822


def test_next_free_port_exhausted():
    with pytest.raises(ValueError):
        config.next_free_port(set(config.STG_PORTS))


def test_valid_platform_has_no_errors():
    errors, warnings = config.validate(platform([app(), app(name="blog", stg_port=4821, hosts=["blog.avkaiexplorer.com",
                                                                                               "api-blog.avkaiexplorer.com"])]))
    assert errors == [] and warnings == []


def test_placeholder_owner_is_a_warning():
    errors, warnings = config.validate(platform([], owner="<owner>"))
    assert errors == [] and any("github_owner" in w for w in warnings)


@pytest.mark.parametrize("bad, fragment", [
    ({"stg_port": 4700}, "stg_port"),
    ({"stg_port": "4820"}, "stg_port"),
    ({"type": "game"}, "type"),
    ({"status": "beta"}, "status"),
    ({"hosts": ["shop.stg.avkaiexplorer.com"]}, "two levels"),
    ({"hosts": ["shop.example.com"]}, "not under"),
    ({"hosts": ["other.avkaiexplorer.com"]}, "must be"),
    ({"name": "www"}, "reserved"),
])
def test_invalid_apps(bad, fragment):
    errors, _ = config.validate(platform([app(**bad)]))
    assert any(fragment in e for e in errors), errors


def test_duplicate_port_and_name():
    errors, _ = config.validate(platform([app(), app()]))
    assert any("duplicate name" in e for e in errors)
    assert any("already used" in e for e in errors)


def test_bad_domain():
    errors, _ = config.validate(platform([], domain="Not A Domain"))
    assert errors


def test_repo_platform_yaml_is_valid():
    errors, _ = config.validate(config.load())
    assert errors == []
