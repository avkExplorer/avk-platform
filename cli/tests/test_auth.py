import json

from avk import auth, dbadmin, keycloak, secretgen


def realm() -> dict:
    return json.loads(auth.REALM_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------- the realm file
def test_realm_file_has_the_required_security_settings():
    r = realm()
    assert r["realm"] == "avk" and r["enabled"]
    assert r["registrationAllowed"] and r["verifyEmail"] and r["resetPasswordAllowed"] and r["loginWithEmailAllowed"]
    assert r["bruteForceProtected"] and not r["permanentLockout"]          # temporary lockout, not a permanent one
    assert "hashAlgorithm(argon2)" in r["passwordPolicy"] and "length(" in r["passwordPolicy"]
    assert r["accessTokenLifespan"] == 300                                  # 5 minutes
    assert r["revokeRefreshToken"] and r["refreshTokenMaxReuse"] == 0       # refresh-token rotation
    assert r["eventsEnabled"] and r["adminEventsEnabled"] and "jboss-logging" in r["eventsListeners"]


def test_realm_file_has_no_secrets_and_no_users():
    text = auth.REALM_FILE.read_text(encoding="utf-8").lower()
    for forbidden in ("password\"", "secret", "\"users\"", "credentials", "clients\""):
        assert forbidden not in text.replace("passwordpolicy", "")


# ---------------------------------------------------------------------------------------------- drift detection
def test_drift_lists_only_the_differing_keys():
    desired = {"a": 1, "b": 2, "smtpServer": {"host": "mailpit", "port": "1025"}}
    actual = {"a": 1, "b": 3, "smtpServer": {"host": "mailpit", "port": "1025", "extra": "x"}}
    assert keycloak.drift(desired, actual) == ["b"]


def test_drift_ignores_the_masked_smtp_password():
    desired = {"smtpServer": {"host": "h", "password": "real-key"}}
    assert keycloak.drift(desired, {"smtpServer": {"host": "h", "password": keycloak.MASK}}) == []
    assert keycloak.drift(desired, {"smtpServer": {"host": "other", "password": keycloak.MASK}}) == ["smtpServer"]


# ---------------------------------------------------------------------------------------------- mail settings
def test_stg_mail_goes_to_mailpit_and_prod_mail_to_resend(tmp_path, monkeypatch):
    stg = auth.smtp_settings("stg")
    assert stg["host"] == "mailpit" and stg["auth"] == "false" and stg["from"] == "noreply@notify.avkaiexplorer.com"
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    mail = tmp_path / "secrets" / "prod" / "platform-mail.env"
    mail.parent.mkdir(parents=True)
    mail.write_text("RESEND_API_KEY=re_test_key\n", encoding="utf-8")
    prod = auth.smtp_settings("prod")
    assert prod["host"] == "smtp.resend.com" and prod["ssl"] == "true" and prod["password"] == "re_test_key"


# ---------------------------------------------------------------------------------------------- secrets and database
def test_keycloak_secrets_are_created_once_and_never_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    first = secretgen.ensure_keycloak("stg")
    files = secretgen.keycloak_files("stg")
    before = {k: p.read_text(encoding="utf-8") for k, p in files.items()}
    assert len(first) == 2
    assert secretgen.ensure_keycloak("stg") == []
    assert {k: p.read_text(encoding="utf-8") for k, p in files.items()} == before
    runtime = secretgen.parse_env(before["runtime"])
    admin = secretgen.parse_env(before["admin"])
    assert runtime["KC_DB_USERNAME"] == "keycloak" and len(runtime["KC_DB_PASSWORD"]) == 48
    assert admin["AVK_ADMIN_USERNAME"] == "avk-admin" and admin["AVK_CLI_USERNAME"] == "avk-cli"
    assert "AVK_ADMIN_PASSWORD" not in runtime      # the console admin password never reaches the container


def test_missing_keys_are_added_without_changing_existing_values(tmp_path, monkeypatch):
    monkeypatch.setenv("AVK_HOME", str(tmp_path))
    path = secretgen.keycloak_files("stg")["admin"]
    path.parent.mkdir(parents=True)
    path.write_text("AVK_ADMIN_USERNAME=avk-admin\nAVK_ADMIN_PASSWORD=keepme\n", encoding="utf-8")
    secretgen.ensure_keycloak("stg")
    got = secretgen.parse_env(path.read_text(encoding="utf-8"))
    assert got["AVK_ADMIN_PASSWORD"] == "keepme" and got["AVK_CLI_PASSWORD"]


def test_keycloak_database_sql_is_least_privilege():
    sql = dbadmin.keycloak_sql("abc123")
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "REVOKE ALL ON DATABASE keycloak FROM PUBLIC" in sql
    assert "GRANT CONNECT ON DATABASE keycloak TO keycloak" in sql


# ---------------------------------------------------------------------------------------------- admin console address
class FakeMasterKC:
    def __init__(self, attributes=None):
        self.master = {"realm": "master", "attributes": attributes or {}}
        self.updates = []

    def realm(self, name):
        return self.master

    def update_realm(self, name, rep):
        self.updates.append(rep)
        self.master = rep


def test_master_realm_gets_the_local_console_address_on_prod_only_and_only_once():
    kc = FakeMasterKC()
    assert auth.ensure_master_frontend(kc, "prod") and kc.updates[-1]["attributes"]["frontendUrl"] == "http://127.0.0.1:4910"
    assert auth.ensure_master_frontend(kc, "prod") == [] and len(kc.updates) == 1      # second run: nothing to do
    assert auth.ensure_master_frontend(FakeMasterKC(), "stg") == []                    # stg public address = console address


def test_master_frontend_runs_last_because_it_invalidates_the_session_token():
    import inspect
    src = inspect.getsource(auth.apply_realm)
    assert src.index("ensure_master_frontend") > max(src.index(n) for n in
                                                    ("ensure_roles", "ensure_delete_account", "ensure_admin_otp_flow"))


# ---------------------------------------------------------------------------------------------- the public route
def test_public_route_blocks_admin_paths_and_the_master_realm():
    text = auth.route_yaml("auth.avkaiexplorer.com")
    for path in ("/admin", "/realms/master", "/metrics", "/health"):
        assert f"PathPrefix(`{path}`)" in text
    blocked = text.split("keycloak-blocked:", 1)[1].split("middlewares:", 2)[0]
    assert "priority: 2000" in blocked and "keycloak-hide" in text    # beats the normal route (1000) and rewrites to a 404
    assert "stsSeconds: 31536000" in text and "frameDeny: true" in text


def test_prod_keycloak_service_publishes_only_the_loopback_admin_port():
    from avk import config
    text = (config.REPO_ROOT / "runtime" / "compose.prod.yaml").read_text(encoding="utf-8")
    block = text.split("\n  keycloak:", 1)[1].split("\nsecrets:", 1)[0]
    assert '"127.0.0.1:4910:8080"' in block
    assert "KC_HOSTNAME: https://auth.avkaiexplorer.com" in block and "KC_HOSTNAME_ADMIN: http://127.0.0.1:4910" in block
    assert "KC_PROXY_HEADERS: xforwarded" in block
    assert block.count("ports:") == 1 and "9000" in block            # management port is never published


def test_platform_yaml_lists_the_login_host_for_terraform():
    from avk import config
    assert "auth.avkaiexplorer.com" in config.load().raw.get("platform_hosts", [])


# ---------------------------------------------------------------------------------------------- the compose files
def test_stg_keycloak_service_is_isolated_and_hardened(tmp_path):
    from avk import config
    text = (config.REPO_ROOT / "runtime" / "compose.stg.yaml").read_text(encoding="utf-8")
    block = text.split("  keycloak:", 1)[1].split("\n  index:", 1)[0] if "\n  index:" in text else text.split("  keycloak:", 1)[1]
    assert "127.0.0.1:4810:8080" in block                  # published on loopback only
    assert "KC_HOSTNAME_BACKCHANNEL_DYNAMIC" in block      # issuer = browser URL, token endpoints = internal
    assert "<<: *hardened" in block and "mem_limit" in block
    assert "9000" in block                                 # health is on the management port, which is not published
