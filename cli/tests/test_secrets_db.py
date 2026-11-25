import pytest

from avk import dbadmin, secretgen


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    return tmp_path


def read(path):
    return secretgen.parse_env(path.read_text(encoding="utf-8"))


def test_platform_secrets_created_once(home):
    first = secretgen.ensure_platform()
    assert any("prod/postgres-admin.password" in c for c in first) and any("backup.env" in c for c in first)
    prod_pw = (home / "secrets" / "prod" / "postgres-admin.password").read_text()
    assert secretgen.ensure_platform() == []  # nothing recreated
    assert (home / "secrets" / "prod" / "postgres-admin.password").read_text() == prod_pw
    assert len(read(home / "secrets" / "platform" / "backup.env")["RESTIC_PASSWORD"]) == 64


def test_app_secrets_three_files_with_distinct_passwords(home):
    secretgen.init_app("hello", "prod")
    files = secretgen.app_files("hello", "prod")
    rt, mig, ro = (read(files[k]) for k in ("runtime", "migrator", "ro"))
    assert rt["SPRING_DATASOURCE_USERNAME"] == "hello_rw" and mig["SPRING_DATASOURCE_USERNAME"] == "hello_migrator"
    assert ro["SPRING_DATASOURCE_USERNAME"] == "hello_ro"
    assert len({rt["SPRING_DATASOURCE_PASSWORD"], mig["SPRING_DATASOURCE_PASSWORD"], ro["SPRING_DATASOURCE_PASSWORD"]}) == 3
    assert rt["SPRING_FLYWAY_ENABLED"] == "false" and mig["SPRING_FLYWAY_ENABLED"] == "true"
    assert rt["SPRING_DATASOURCE_URL"] == "jdbc:postgresql://pg-prod:5432/hello"
    # the runtime file never contains the migrator's credentials
    assert mig["SPRING_DATASOURCE_PASSWORD"] not in files["runtime"].read_text()


def test_existing_values_are_kept_unless_rotated(home):
    secretgen.init_app("hello", "stg")
    files = secretgen.app_files("hello", "stg")
    before = {k: read(p) for k, p in files.items()}
    msgs = secretgen.init_app("hello", "stg")
    assert all(m.startswith("kept") for m in msgs)
    assert {k: read(p) for k, p in files.items()} == before
    secretgen.init_app("hello", "stg", rotate={"runtime"})
    after = {k: read(p) for k, p in files.items()}
    assert after["runtime"]["SPRING_DATASOURCE_PASSWORD"] != before["runtime"]["SPRING_DATASOURCE_PASSWORD"]
    assert after["migrator"] == before["migrator"] and after["ro"] == before["ro"]


def test_messages_never_contain_values(home):
    msgs = secretgen.init_app("hello", "prod")
    password = read(secretgen.app_files("hello", "prod")["runtime"])["SPRING_DATASOURCE_PASSWORD"]
    assert not any(password in m for m in msgs)


def test_credential_detection():
    assert secretgen.is_credential("OIDC_CLIENT_SECRET") and secretgen.is_credential("db_password")
    assert not secretgen.is_credential("OIDC_ISSUER")


def test_sql_grants_least_privilege():
    server, database = dbadmin.build_sql("hello", {"migrator": "m" * 8, "rw": "w" * 8, "ro": "r" * 8})
    assert "CREATE ROLE hello_rw LOGIN PASSWORD 'wwwwwwww' NOSUPERUSER NOCREATEDB NOCREATEROLE" in server
    assert "REVOKE ALL ON DATABASE hello FROM PUBLIC" in server
    assert "OWNER hello_migrator" in server
    # rw: data only; ro: select only; neither can create objects or touch the schema
    assert "TO hello_rw" in database and "GRANT SELECT ON ALL TABLES IN SCHEMA public TO hello_ro" in database
    assert "CREATE ON SCHEMA" not in database and "ALL PRIVILEGES" not in database
    assert "TRUNCATE" not in database and "DROP" not in database


def test_missing_secret_files_stop_db_create(home):
    with pytest.raises(SystemExit, match="secrets init"):
        dbadmin.passwords_from_files("hello", "prod")


def test_db_create_uses_stdin_not_argv(home, monkeypatch):
    secretgen.init_app("hello", "stg")
    calls = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(dbadmin.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or Done())
    assert dbadmin.create("hello", "stg") == 0
    password = read(secretgen.app_files("hello", "stg")["runtime"])["SPRING_DATASOURCE_PASSWORD"]
    for argv, kw in calls:
        assert password not in " ".join(argv)           # never on a command line
    assert any(password in kw["input"] for _, kw in calls)  # only on stdin
