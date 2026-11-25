"""avk new-app / retire-app: names are validated, the plan is shown before anything outward-facing, a second run changes nothing,
adoption never overwrites, and nothing outside is touched without the confirmation. git, gh, docker, terraform and other avk
commands are faked."""

import json
from pathlib import Path

import pytest
import yaml

from avk import config, newapp, template

PLATFORM = """# platform registry (a comment that must survive)
domain: avkaiexplorer.com
github_owner: avkExplorer

platform_hosts:
  - auth.avkaiexplorer.com

apps:
  - name: hello
    type: web
    stg_port: 4820
    repo: avkExplorer/hello
    hosts: [hello.avkaiexplorer.com]
    status: stg
"""


class FakeShell:
    """Records every command. `answers` maps a command prefix to (code, output); anything else succeeds with no output."""

    def __init__(self, repo_exists=False, tf_plan="Plan: 2 to add, 1 to change, 0 to destroy.\n  # cloudflare_dns_record.tunnel_host[\"x\"] will be created\n",
                 stg_up=False, git_dirty=True, branch="main"):
        self.calls, self.repo_exists, self.tf_plan, self.stg_up = [], repo_exists, tf_plan, stg_up
        self.git_dirty, self.branch = git_dirty, branch

    def run(self, argv, cwd=None, timeout=1800):
        self.calls.append(" ".join(str(a) for a in argv))
        head = argv[:3]
        if argv[:2] == ["git", "init"] and cwd is not None:
            (Path(cwd) / ".git").mkdir(exist_ok=True)                    # a repository now exists, as after the real command
        if head[:2] == ["gh", "repo"] and head[2] == "view":
            return newapp.Result(0 if self.repo_exists else 1)
        if argv[:2] == ["git", "status"]:
            return newapp.Result(0, " M x\n" if self.git_dirty else "")
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return newapp.Result(0, self.branch + "\n")
        if argv[0] == "docker" and argv[1] == "ps":
            return newapp.Result(0, "Up 5 minutes\n" if self.stg_up else "")
        return newapp.Result(0)

    def avk(self, *args, timeout=1800):
        self.calls.append("avk " + " ".join(args))
        if args[:2] == ("tf", "plan"):
            return newapp.Result(0, self.tf_plan)
        if args[:2] == ("legal", "render"):
            return newapp.Result(0, "ok    x: pages; 5 file(s) written, 0 unchanged\n")
        return newapp.Result(0)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    (tmp_path / "platform.yaml").write_text(PLATFORM, encoding="utf-8")
    (tmp_path / "workspace").mkdir()
    said = []
    shell = FakeShell()
    c = newapp.Context(workspace=tmp_path / "workspace", platform_file=tmp_path / "platform.yaml", shell=shell,
                       ask=lambda text: True, say=said.append, state_dir=tmp_path / "state", template_ref="main")
    c.said, c.fake = said, shell
    return c


def apps(c):
    return {a["name"]: a for a in yaml.safe_load(c.platform_file.read_text(encoding="utf-8"))["apps"]}


# ------------------------------------------------------------------------------------------------- names and platform.yaml
@pytest.mark.parametrize("name", ["Bad_Name", "www", "api-x", "lab-x", "a", "x" * 31, "-x", "x--y"])
def test_bad_names_are_rejected_before_anything_is_touched(ctx, name):
    code, _ = newapp.run_new_app(newapp.Options(name=name), ctx)
    assert code == 1 and ctx.fake.calls == []
    assert apps(ctx).keys() == {"hello"}


def test_unknown_type_is_rejected(ctx):
    assert newapp.run_new_app(newapp.Options(name="demo", app_type="mobile"), ctx)[0] == 1


def test_the_new_entry_is_appended_and_comments_and_order_survive(ctx):
    newapp.add_app_entry(ctx.platform_file, newapp.entry_text(newapp.Options(name="demo", app_type="api"), 4821, "avkExplorer", "avkaiexplorer.com"))
    text = ctx.platform_file.read_text(encoding="utf-8")
    assert text.startswith("# platform registry (a comment that must survive)")
    assert apps(ctx)["demo"]["hosts"] == ["demo.avkaiexplorer.com"] and apps(ctx)["demo"]["stg_port"] == 4821
    errors, _ = config.validate(config.load(ctx.platform_file))
    assert errors == []


def test_status_can_be_changed_in_place(ctx):
    assert newapp.set_app_status(ctx.platform_file, "hello", "retired") is True
    assert apps(ctx)["hello"]["status"] == "retired"
    assert newapp.set_app_status(ctx.platform_file, "hello", "retired") is False


# --------------------------------------------------------------------------------------------------------------- the plan
def test_dry_run_shows_the_confirmations_and_changes_nothing(ctx):
    before = ctx.platform_file.read_text(encoding="utf-8")
    code, _ = newapp.run_new_app(newapp.Options(name="pingpong", app_type="api", dry_run=True), ctx)
    said = "\n".join(ctx.said)
    assert code == 0 and ctx.platform_file.read_text(encoding="utf-8") == before
    assert not (ctx.workspace / "pingpong").exists()
    assert "create the PUBLIC GitHub repository avkExplorer/pingpong" in said and "apply Terraform: Plan: 2 to add" in said
    assert "create the prod database" in said and "create the prod login client" in said
    outward = [c for c in ctx.fake.calls if c.startswith(("git", "gh repo create")) or "tf apply" in c or "db create" in c]
    assert outward == []


def test_declining_puts_platform_yaml_back_and_does_nothing_outward(ctx):
    before = ctx.platform_file.read_text(encoding="utf-8")
    ctx.ask = lambda text: False
    code, _ = newapp.run_new_app(newapp.Options(name="pingpong", app_type="api"), ctx)
    assert code == 2 and ctx.platform_file.read_text(encoding="utf-8") == before
    assert not [c for c in ctx.fake.calls if "tf apply" in c or "repo create" in c or c.startswith("git")]


# ------------------------------------------------------------------------------------------------------------- the real run
def test_a_new_api_app_goes_through_every_step_in_order(ctx):
    code, report = newapp.run_new_app(newapp.Options(name="pingpong", app_type="api", yes=True), ctx)
    assert code == 0
    folder = ctx.workspace / "pingpong"
    assert (folder / "avk.yaml").is_file() and (folder / "src/main/java/com/avkaiexplorer/pingpong/auth/MeController.java").is_file()
    assert not (folder / "src/main/resources/static").exists()                      # an API has no pages
    assert apps(ctx)["pingpong"]["stg_port"] == 4821 and apps(ctx)["pingpong"]["type"] == "api"
    calls = ctx.fake.calls
    order = [next(i for i, c in enumerate(calls) if key in c) for key in
             ("tf plan", "git init", "gh repo create", "repo-settings.ps1", "tf apply --yes", "secrets init pingpong --env stg",
              "db create pingpong --env prod", "auth client pingpong --env prod", "stg up pingpong --build")]
    assert order == sorted(order)
    assert not [c for c in calls if "legal render" in c or "legal smtp" in c]       # an API has no policy pages or contact form
    assert "git update-index --chmod=+x mvnw" in calls                              # the pipeline runs ./mvnw on Linux
    assert any(s == "done" and st == "stg" for s, st, _ in report.lines)
    assert "completed in" in "\n".join(ctx.said)


def test_a_second_run_says_nothing_to_do_and_touches_nothing(ctx):
    newapp.run_new_app(newapp.Options(name="pingpong", app_type="api", yes=True), ctx)
    second = FakeShell(repo_exists=True, tf_plan="No changes. Your infrastructure matches the configuration.", stg_up=True, git_dirty=False)
    ctx.shell = second
    before = ctx.platform_file.read_text(encoding="utf-8")
    code, report = newapp.run_new_app(newapp.Options(name="pingpong", app_type="api", yes=True), ctx)
    assert code == 0 and ctx.platform_file.read_text(encoding="utf-8") == before
    assert not report.changed() and "nothing to do" in "\n".join(ctx.said)
    forbidden = ("repo create", "tf apply", "secrets init", "db create", "auth client", "stg up", "git commit", "git init")
    assert not [c for c in second.calls if any(f in c for f in forbidden)]


def test_a_web_app_gets_policy_pages_and_mail_settings(ctx):
    code, _ = newapp.run_new_app(newapp.Options(name="shop", app_type="web", yes=True), ctx)
    assert code == 0
    calls = ctx.fake.calls
    assert any("legal render shop" in c for c in calls) and any("legal smtp shop --env prod" in c for c in calls)
    assert (ctx.workspace / "shop/src/main/resources/static/index.html").is_file()


def test_a_static_site_has_no_database_login_or_mail_and_gets_the_nginx_template(ctx):
    code, _ = newapp.run_new_app(newapp.Options(name="demosite", app_type="static", yes=True), ctx)
    assert code == 0
    folder = ctx.workspace / "demosite"
    assert (folder / "nginx.conf").is_file() and (folder / "site/index.html").is_file() and not (folder / "pom.xml").exists()
    assert "avk-legal-footer:start" in (folder / "site/index.html").read_text(encoding="utf-8")
    calls = ctx.fake.calls
    assert not [c for c in calls if any(k in c for k in ("db create", "auth client", "legal smtp", "secrets init"))]
    assert any("legal render demosite" in c for c in calls)


# --------------------------------------------------------------------------------------------------------------- adoption
def test_adopting_an_existing_folder_reports_differences_and_never_overwrites(ctx):
    folder = ctx.workspace / "pingpong"
    (folder / "src").mkdir(parents=True)
    (folder / ".git").mkdir()                                              # an existing repository: changes go to a branch
    (folder / "avk.yaml").write_text("name: pingpong\n# mine\n", encoding="utf-8")
    ctx.fake = ctx.shell = FakeShell(repo_exists=True, tf_plan="No changes.", git_dirty=True, branch="main")
    code, report = newapp.run_new_app(newapp.Options(name="pingpong", app_type="api", yes=True), ctx)
    assert code == 0
    assert (folder / "avk.yaml").read_text(encoding="utf-8") == "name: pingpong\n# mine\n"               # left alone
    assert any(st == "template" and s == "note" and "avk.yaml" in d for s, st, d in report.lines)
    assert any("git checkout -b avk-onboarding" in c for c in ctx.shell.calls), ctx.shell.calls                          # changes go to a branch + PR
    assert any("gh pr create" in c for c in ctx.shell.calls) and not any("repo create" in c for c in ctx.shell.calls)


def test_update_template_overwrites_the_differing_files(ctx):
    folder = ctx.workspace / "pingpong"
    folder.mkdir()
    (folder / "avk.yaml").write_text("old\n", encoding="utf-8")
    ctx.fake = ctx.shell = FakeShell(repo_exists=True, tf_plan="No changes.")
    newapp.run_new_app(newapp.Options(name="pingpong", app_type="api", yes=True, update_template=True), ctx)
    assert "name: pingpong" in (folder / "avk.yaml").read_text(encoding="utf-8")


def test_an_adopted_static_site_keeps_its_pages_and_gets_the_footer_marker(ctx):
    folder = ctx.workspace / "clickers"
    folder.mkdir()
    (folder / "index.html").write_text("<html><body><h1>mine</h1></body></html>", encoding="utf-8")
    (folder / "404.html").write_text("<html><body>nope</body></html>", encoding="utf-8")
    ctx.fake = ctx.shell = FakeShell(repo_exists=True, tf_plan="No changes.")
    code, _ = newapp.run_new_app(newapp.Options(name="clickers", app_type="static", yes=True), ctx)
    assert code == 0
    page = (folder / "index.html").read_text(encoding="utf-8")
    assert "<h1>mine</h1>" in page and page.count("avk-legal-footer:start") == 1
    assert not (folder / "site").exists()                                             # the site is the repository root
    assert "COPY ./ /usr/share/nginx/html/" in (folder / "Dockerfile").read_text(encoding="utf-8")


def test_footer_markers_are_added_once_and_a_page_without_body_is_refused(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html><body>x</body></html>", encoding="utf-8")
    assert newapp.ensure_footer_markers(page) is True and newapp.ensure_footer_markers(page) is False
    page.write_text("<p>no body tag</p>", encoding="utf-8")
    with pytest.raises(newapp.NewAppError):
        newapp.ensure_footer_markers(page)


# ------------------------------------------------------------------------------------------------------------- retire-app
def test_retire_lists_everything_and_needs_the_typed_phrase(ctx):
    assert newapp.run_retire_app("hello", None, ctx) == 1
    said = "\n".join(ctx.said)
    assert "final backup" in said and "ARCHIVE the GitHub repository" in said and "KEPT" in said
    assert ctx.fake.calls == [] and apps(ctx)["hello"]["status"] == "stg"
    assert newapp.run_retire_app("hello", "retire something-else", ctx) == 1 and ctx.fake.calls == []


def test_retire_backs_up_first_then_removes_and_archives_never_deletes(ctx):
    assert newapp.run_retire_app("hello", "retire hello", ctx) == 0
    calls = ctx.fake.calls
    assert calls[0] == "avk backup run"
    assert apps(ctx)["hello"]["status"] == "retired"
    assert any("tf apply --yes" in c for c in calls) and any("repo archive" in c for c in calls)
    assert not any("repo delete" in c or "drop database" in c.lower() for c in calls)
    assert any("auth client-delete hello --env prod" in c for c in calls)


def test_a_failed_backup_stops_everything(ctx):
    class Bad(FakeShell):
        def avk(self, *args, timeout=1800):
            self.calls.append("avk " + " ".join(args))
            return newapp.Result(1 if args[:2] == ("backup", "run") else 0)

    ctx.shell = ctx.fake = Bad()
    assert newapp.run_retire_app("hello", "retire hello", ctx) == 1
    assert apps(ctx)["hello"]["status"] == "stg" and not any("docker" in c for c in ctx.fake.calls)


def test_retiring_an_unknown_or_already_retired_app_is_harmless(ctx):
    assert newapp.run_retire_app("ghost", "retire ghost", ctx) == 1
    newapp.set_app_status(ctx.platform_file, "hello", "retired")
    assert newapp.run_retire_app("hello", "retire hello", ctx) == 0 and ctx.fake.calls == []


# ---------------------------------------------------------------------------------------------------------------- template
def test_every_template_combination_renders_without_leftover_placeholders(tmp_path):
    for app_type in ("web", "api", "static"):
        for auth in (True, False):
            for db in (True, False):
                dest = tmp_path / f"{app_type}-{auth}-{db}"
                flags = template.flags_for(app_type, auth, db)
                template.render(dest, "combo-app", flags, "avkExplorer", "avkaiexplorer.com", "main", 4890,
                                template_dir=template.template_dir_for(app_type))
                assert template.leftover_tokens(dest) == [], (app_type, auth, db)
                yaml.safe_load((dest / "avk.yaml").read_text(encoding="utf-8"))
                json.loads((dest / "wall.json").read_text(encoding="utf-8"))


def test_pinned_workflow_ref_changes_the_signing_identity_the_caller_expects(tmp_path):
    dest = tmp_path / "x"
    template.render(dest, "demo", template.flags_for("api"), "avkExplorer", "avkaiexplorer.com", "a" * 40, 4890)
    text = (dest / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert f"release.yml@{'a' * 40}" in text and "refs/heads/main" not in text
    template.render(tmp_path / "y", "demo", template.flags_for("api"), "avkExplorer", "avkaiexplorer.com", "main", 4890)
    assert "release.yml@refs/heads/main" in (tmp_path / "y/.github/workflows/release.yml").read_text(encoding="utf-8")


def test_rendering_twice_changes_nothing(tmp_path):
    args = dict(name="demo", flags=template.flags_for("api"), owner="avkExplorer", domain="avkaiexplorer.com", platform_ref="main", stg_port=4890)
    template.render(tmp_path / "x", **args)
    second = template.render(tmp_path / "x", **args)
    assert not second.created and not second.updated and not second.differs


# ---------------------------------------------------------------------------- apps without secrets (static sites) at runtime
def test_an_app_with_no_migrations_and_no_env_runs_without_an_env_file():
    from avk import stg
    from avk.deployer import dockerops

    assert stg.needs_secrets({"port": 8080}) is False and stg.needs_secrets({"env": []}) is False
    assert stg.needs_secrets({"migrations": {"command": ["x"]}}) is True and stg.needs_secrets({"env": ["A"]}) is True
    assert "--env-file" not in stg.run_args("site", 4850, 8080, None, "img")
    assert "--env-file" in stg.run_args("site", 4850, 8080, Path("x.env"), "img")
    seen = []
    docker = dockerops.Docker(run=lambda argv, stdin=None: (seen.append(argv), (0, "", ""))[1])
    docker.start_app("site-blue", "img@sha256:x", None, {"avk.app": "site"})
    assert "--env-file" not in seen[-1] and "--read-only" in seen[-1] and "--cap-drop" in seen[-1]


def test_static_apps_are_not_scraped_for_metrics_but_their_logs_are_tailed():
    from avk import obs

    rows = [{"id": "a" * 64, "name": "clickers-blue", "app": "clickers", "service": ""},
            {"id": "b" * 64, "name": "hello-green", "app": "hello", "service": ""}]
    metrics, logs_ = obs.build_targets(rows, frozenset({"clickers"}))
    assert [m["labels"]["app"] for m in metrics] == ["hello"] and [t["labels"]["app"] for t in logs_] == ["clickers", "hello"]


def test_the_wall_step_judges_only_the_new_app_not_unrelated_folders(ctx, tmp_path):
    (ctx.workspace / "wall").mkdir()
    (ctx.workspace / "wall" / "wall.py").write_text("# fake", encoding="utf-8")

    class WallShell(FakeShell):
        def __init__(self, mine_ok=True, check_ok=True, **kw):
            super().__init__(**kw)
            self.mine_ok, self.check_ok = mine_ok, check_ok

        def run(self, argv, cwd=None, timeout=1800):
            if len(argv) > 1 and str(argv[1]).endswith("wall.py"):
                self.calls.append("wall " + " ".join(argv[2:]))
                if argv[2] == "validate":
                    first = "ok        pingpong   pingpong" if self.mine_ok else "BAD       pingpong   pingpong"
                    return newapp.Result(1, first + "\nMISSING   docs   no wall.json\n")      # exit 1 because of the unrelated folder
                return newapp.Result(0 if self.check_ok else 1, "")
            return super().run(argv, cwd, timeout)

    ctx.shell = ctx.fake = WallShell(mine_ok=True)
    assert newapp.run_new_app(newapp.Options(name="pingpong", app_type="api", yes=True), ctx)[0] == 0
    assert "wall check pingpong" in ctx.fake.calls
    ctx.shell = ctx.fake = WallShell(mine_ok=False)
    with pytest.raises(newapp.NewAppError):
        newapp.run_new_app(newapp.Options(name="pingpong", app_type="api", yes=True), ctx)


def test_footer_markers_go_before_the_last_body_tag_not_one_inside_a_script_string(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<html><body><script>var s='<html><body>'+x+'</body></html>';</script><p>real</p></body></html>", encoding="utf-8")
    assert newapp.ensure_footer_markers(page) is True
    text = page.read_text(encoding="utf-8")
    assert "'</body></html>';</script>" in text                       # the JavaScript string is untouched
    assert text.index("avk-legal-footer:start") > text.index("<p>real</p>")
    assert "/legal/footer.css" in text and "/legal/legal.css" not in text      # the scoped stylesheet: no global restyling of the page


def test_the_static_dockerfile_names_its_user_so_the_dockerfile_scan_passes(tmp_path):
    template.render(tmp_path / "s", "demo", template.flags_for("static"), "avkExplorer", "avkaiexplorer.com", "main", 4890,
                    template_dir=template.template_dir_for("static"))
    lines = [l.strip() for l in (tmp_path / "s/Dockerfile").read_text(encoding="utf-8").splitlines()]
    assert "USER 101:101" in lines and any(l.startswith("HEALTHCHECK") for l in lines)      # Checkov CKV_DOCKER_3 and _2
    assert [l for l in lines if l.startswith("USER")][-1] == "USER 101:101"                    # the LAST user is the unprivileged one
    assert "RUN apk upgrade --no-cache" in lines                                               # base-image fixes released since it was built
    assert next(l for l in lines if l.startswith("FROM")).count("@sha256:") == 1               # a pinned base image

