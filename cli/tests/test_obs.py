"""Observability stack: pinned and private by construction, dashboards query metrics that exist, targets and secrets behave."""

import json
import re
from types import SimpleNamespace

import pytest
import yaml

from avk import config, logs, obs

OBS = config.REPO_ROOT / "runtime" / "obs"


def compose():
    return yaml.safe_load((OBS / "compose.obs.yaml").read_text(encoding="utf-8"))


def test_every_obs_image_is_pinned_by_digest():
    images = [s["image"] for s in compose()["services"].values()]
    assert len(images) == 5 and all("@sha256:" in i for i in images)


def test_only_grafana_is_published_and_only_on_loopback():
    published = {name: s.get("ports", []) for name, s in compose()["services"].items() if s.get("ports")}
    assert published == {"grafana": ["127.0.0.1:4950:3000"]}


def test_the_docker_socket_is_never_mounted_and_everything_is_hardened():
    text = (OBS / "compose.obs.yaml").read_text(encoding="utf-8")
    assert "docker.sock" not in text
    for name, service in compose()["services"].items():
        assert service["read_only"] is True and service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        assert service.get("mem_limit"), name                                    # nothing can eat the PC's memory unbounded
    for name, service in compose()["services"].items():
        for volume in service.get("volumes", []):
            assert not (isinstance(volume, str) and re.match(r"^(/|[A-Za-z]:)[^:]*:[^:]+$", volume)), (name, volume)   # no writable host path


def test_the_log_files_are_mounted_read_only():
    mounts = [v for v in compose()["services"]["alloy"]["volumes"] if "/var/lib/docker/containers" in v]
    assert mounts and all(v.endswith(":ro") for v in mounts)


def test_config_files_parse_and_prometheus_scrapes_what_the_dashboard_needs():
    prom = yaml.safe_load((OBS / "prometheus.yml").read_text(encoding="utf-8"))
    jobs = {j["job_name"] for j in prom["scrape_configs"]}
    assert {"apps", "traefik", "postgres"} <= jobs
    assert next(j for j in prom["scrape_configs"] if j["job_name"] == "apps")["metrics_path"] == "/actuator/prometheus"
    loki = yaml.safe_load((OBS / "loki.yaml").read_text(encoding="utf-8"))
    assert loki["limits_config"]["retention_period"] == "336h"
    for f in (OBS / "grafana" / "provisioning").rglob("*.yaml"):
        assert yaml.safe_load(f.read_text(encoding="utf-8"))["apiVersion"] == 1


def dashboard():
    return json.loads((OBS / "grafana" / "dashboards" / "app-overview.json").read_text(encoding="utf-8"))


def test_the_dashboard_has_every_panel_the_spec_asks_for():
    titles = [p["title"] for p in dashboard()["panels"]]
    for needed in ("Request rate", "Error rate (5xx)", "Latency p50 / p95", "Database connections", "JVM heap memory", "Logs"):
        assert needed in titles
    assert dashboard()["templating"]["list"][0]["name"] == "app"                  # one dashboard, one variable: a dashboard per app


def test_dashboard_queries_only_use_metrics_the_stack_really_exports():
    known = ("http_server_requests_seconds_count", "http_server_requests_seconds_bucket", "hikaricp_connections_active",
             "hikaricp_connections_idle", "hikaricp_connections_pending", "hikaricp_connections_max", "jvm_memory_used_bytes",
             "jvm_memory_max_bytes", "traefik_service_requests_total", "pg_stat_database_numbackends")
    for panel in dashboard()["panels"]:
        for target in panel["targets"]:
            expr = target["expr"]
            if panel["datasource"]["type"] == "prometheus":
                assert any(k in expr for k in known), expr
            assert panel["datasource"]["uid"] in ("prom", "loki")


def test_targets_scrape_only_containers_with_an_app_label_and_tail_all():
    rows = [{"id": "a" * 64, "name": "hello-green", "app": "hello", "service": ""},
            {"id": "b" * 64, "name": "avk-prod-traefik-1", "app": "", "service": "traefik"}]
    metrics, logs_ = obs.build_targets(rows)
    assert metrics == [{"targets": ["hello-green:8081"], "labels": {"app": "hello", "container": "hello-green"}}]
    assert [t["labels"]["app"] for t in logs_] == ["hello", "traefik"]
    assert logs_[0]["labels"]["__path__"] == f"/var/lib/docker/containers/{'a' * 64}/{'a' * 64}-json.log"


def test_write_targets_replaces_files_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    assert obs.write_targets([{"id": "c" * 64, "name": "hello-blue", "app": "hello", "service": ""}]) == (1, 1)
    assert json.loads((tmp_path / "state" / "obs" / "apps.json").read_text())[0]["targets"] == ["hello-blue:8081"]
    assert not list((tmp_path / "state" / "obs").glob("*.tmp"))


def test_monitor_role_can_read_statistics_only():
    sql = obs.monitor_sql("deadbeef")
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql and "GRANT pg_monitor" in sql and "CONNECTION LIMIT" in sql
    assert "GRANT ALL" not in sql and "SUPERUSER " not in sql.replace("NOSUPERUSER", "")


def test_secrets_are_created_once_and_never_printed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    calls = []
    fake = lambda env, db, sql: (calls.append(sql), SimpleNamespace(returncode=0, stderr=""))[1]
    assert obs.ensure_secrets(psql=fake) == ["grafana.env", "postgres-monitor.env"]
    assert obs.ensure_secrets(psql=fake) == [] and len(calls) == 1                 # second run changes nothing
    grafana = (tmp_path / "secrets" / "prod" / "grafana.env").read_text()
    monitor = (tmp_path / "secrets" / "prod" / "postgres-monitor.env").read_text()
    assert grafana.startswith("GF_SECURITY_ADMIN_PASSWORD=") and monitor.startswith("DATA_SOURCE_NAME=postgresql://avk_monitor:")
    out = capsys.readouterr().out
    assert grafana.split("=", 1)[1].strip() not in out


def test_a_failed_role_creation_stops_without_writing_the_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    with pytest.raises(SystemExit):
        obs.ensure_secrets(psql=lambda env, db, sql: SimpleNamespace(returncode=1, stderr="boom\n"))
    assert not (tmp_path / "secrets" / "prod" / "postgres-monitor.env").exists()


def test_traefik_exposes_metrics_on_a_private_entrypoint_that_is_not_published():
    prod = yaml.safe_load((config.REPO_ROOT / "runtime" / "compose.prod.yaml").read_text(encoding="utf-8"))
    traefik = prod["services"]["traefik"]
    assert "--metrics.prometheus=true" in traefik["command"] and "--entrypoints.metrics.address=:8083" in traefik["command"]
    assert traefik["ports"] == ["127.0.0.1:4900:80"]                                # 8083 is never published


def test_logs_command_filters_by_request_id_and_validates_input(capsys):
    lines = ['{"request_id":"abc-12345678","message":"x"}', '{"request_id":"other-0000001","message":"y"}']
    assert logs.filter_lines(lines, "abc-12345678") == [lines[0]] and logs.filter_lines(lines, None) == lines
    ns = lambda **kw: SimpleNamespace(**{"app": "hello", "env": "prod", "since": "15m", "request_id": None, "tail": 10, **kw})
    assert logs.main(ns(since="forever")) == 1
    assert logs.main(ns(request_id='x"; rm -rf')) == 1
    assert logs.main(ns(app="not-an-app")) == 1
    assert "must look like" in capsys.readouterr().out


def test_obs_ports_are_registered_in_the_platform_port_list():
    assert config.PLATFORM_PORTS[4950] == "Grafana"


def test_missing_docker_labels_are_not_mistaken_for_an_app(monkeypatch):
    out = "a" * 64 + "|/avk-prod-traefik-1|<no value>|traefik\n" + "b" * 64 + "|/hello-green|hello|<no value>\n"
    monkeypatch.setattr(obs.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out, returncode=0))
    rows = obs.inspect_containers(["x"])
    assert rows[0]["app"] == "" and rows[0]["service"] == "traefik" and rows[1]["app"] == "hello" and rows[1]["service"] == ""
    assert len(obs.build_targets(rows)[0]) == 1


def test_only_alloy_gets_an_extra_capability_and_only_one():
    extra = {name: s["cap_add"] for name, s in compose()["services"].items() if s.get("cap_add")}
    assert extra == {"alloy": ["DAC_OVERRIDE"]}


def test_the_error_rate_panel_shows_zero_instead_of_no_data_when_there_are_no_errors():
    panel = next(p for p in dashboard()["panels"] if p["title"] == "Error rate (5xx)")
    assert "or vector(0)" in panel["targets"][0]["expr"]
