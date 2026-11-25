"""`avk new-app <name>` and `avk retire-app <name>`: onboarding and offboarding of an app on the platform.

`new-app` takes an app from nothing (or from an existing folder: "adopt") to running on stg, wired to the pipeline, DNS, login, database,
email alias and policy pages. Every step first checks what already exists and skips or repairs it, so the command can be run again after
any failure and a second run on a finished app says "nothing to do". It prints the plan, asks for the outward-facing confirmations ONCE
(create a public GitHub repo, apply Terraform, create the prod database and login client), and then runs.

All outside effects (git, gh, docker, terraform, other `avk` commands) go through `Shell`, so the tests fake them.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import yaml

from avk import config, template

REPO_SETTINGS_CHECKS = "ci / gates"
ATTRIBUTION = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
ADOPT_BRANCH = "avk-onboarding"


class NewAppError(RuntimeError):
    """A step failed or a prerequisite is missing; the message says what to do."""


# ------------------------------------------------------------------------------------------------------------- shell
@dataclass
class Result:
    code: int
    out: str = ""


class Shell:
    """Runs commands. `avk(...)` runs another avk command with the same Python."""

    def run(self, argv: list[str], cwd: Path | None = None, timeout: int = 1800) -> Result:
        try:
            done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Result(127, f"{type(exc).__name__}: {exc}")
        return Result(done.returncode, (done.stdout or "") + (done.stderr or ""))

    def avk(self, *args: str, timeout: int = 1800) -> Result:
        return self.run([sys.executable, "-m", "avk", *args], cwd=config.REPO_ROOT, timeout=timeout)


# ---------------------------------------------------------------------------------------------------------- options
@dataclass
class Options:
    name: str
    app_type: str = "web"
    auth: bool = True
    db: bool = True
    update_template: bool = False
    yes: bool = False
    dry_run: bool = False
    site: str | None = None            # static sites: folder holding the pages ("." = the repository root)


@dataclass
class Context:
    workspace: Path = field(default_factory=lambda: config.REPO_ROOT.parent)
    platform_file: Path = field(default_factory=lambda: config.PLATFORM_FILE)
    shell: Shell = field(default_factory=Shell)
    ask: Callable[[str], bool] = lambda text: input(text + " [y/N] ").strip().lower() in ("y", "yes")
    say: Callable[[str], None] = print
    clock: Callable[[], float] = time.time
    template_ref: str | None = None    # platform workflow ref for the caller workflows; default: platform.yaml `workflow_ref`, else main
    state_dir: Path = field(default_factory=lambda: config.avk_home() / "state" / "newapp")


@dataclass
class Report:
    lines: list[tuple[str, str, str]] = field(default_factory=list)   # (status, step, detail)

    def add(self, status: str, step: str, detail: str = "") -> None:
        self.lines.append((status, step, detail))

    def changed(self) -> bool:
        return any(s in ("done", "todo") for s, _, _ in self.lines)


def load_state(ctx: Context, name: str) -> dict:
    try:
        return json.loads((ctx.state_dir / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_done(ctx: Context, name: str, step: str) -> None:
    state = load_state(ctx, name)
    state[step] = True
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    (ctx.state_dir / f"{name}.json").write_text(json.dumps(state), encoding="utf-8")


def stg_running(shell: Shell, name: str) -> bool:
    res = shell.run(["docker", "ps", "--filter", f"name=^{name}-stg$", "--format", "{{.Status}}"])
    return res.code == 0 and res.out.strip().startswith("Up")


def validate(opts: Options) -> list[str]:
    errors = [f"name {e}" for e in config.name_errors(opts.name)]
    if opts.app_type not in config.APP_TYPES:
        errors.append(f"type must be one of {sorted(config.APP_TYPES)}")
    if opts.app_type == "static" and (not opts.auth or not opts.db):
        pass                                              # harmless: a static site has neither
    return errors


# ------------------------------------------------------------------------------------------------------- platform.yaml
def entry_text(opts: Options, port: int, owner: str, domain: str) -> str:
    return (f"  - name: {opts.name}\n    type: {opts.app_type}\n    stg_port: {port}\n    repo: {owner}/{opts.name}\n"
            f"    hosts: [{opts.name}.{domain}]\n    status: stg\n")


def add_app_entry(path: Path, text: str) -> None:
    """Append one app to the `apps:` list without touching the rest of the file (comments and order stay)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if re.match(r"^apps:\s*(#.*)?$", l)), None)
    if start is None:
        raise NewAppError(f"{path.name} has no `apps:` list")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t", "#")):
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():             # insert before trailing blank lines
        end -= 1
    if lines[end - 1] and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    lines[end:end] = [text]
    path.write_text("".join(lines), encoding="utf-8", newline="")


def find_app(platform: config.Platform, name: str) -> dict | None:
    return next((a for a in platform.apps if str(a.get("name")) == name), None)


def set_app_status(path: Path, name: str, status: str) -> bool:
    """Change `status:` of one app in place. Returns whether anything changed."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_app, changed = False, False
    for i, line in enumerate(lines):
        m = re.match(r"^\s*-\s+name:\s*(\S+)", line)
        if m:
            in_app = m.group(1) == name
        elif in_app and re.match(r"^\s+status:\s*\S+", line):
            new = re.sub(r"(status:\s*)\S+", rf"\g<1>{status}", line, count=1)
            changed = changed or new != line
            lines[i] = new
    if changed:
        path.write_text("".join(lines), encoding="utf-8", newline="")
    return changed


# ---------------------------------------------------------------------------------------------------- static helpers
FOOTER_BLOCK = ("<!-- avk-legal-footer:start -->\n<!-- avk-legal-footer:end -->\n<link rel=\"stylesheet\" href=\"/legal/footer.css\">\n"
                "<script src=\"/legal/consent.js\" data-mode=\"notice\" defer></script>\n")


def ensure_footer_markers(index: Path) -> bool:
    """Adopted pages get the place where `avk legal render` writes the footer links, just before the LAST </body>. (Pages that build
    their own documents in JavaScript contain </body> inside strings earlier on: only the final one closes the real page.)
    The stylesheet is the footer-only one, so the page's own design is untouched. Returns True if it edited."""
    text = index.read_text(encoding="utf-8")
    if "avk-legal-footer:start" in text:
        return False
    closers = list(re.finditer(r"</body\s*>", text, re.I))
    if not closers:
        raise NewAppError(f"{index} has no </body>: add `{FOOTER_BLOCK.splitlines()[0]}` and the end marker where the footer links go")
    at = closers[-1].start()
    index.write_text(text[:at] + FOOTER_BLOCK + text[at:], encoding="utf-8", newline="")
    return True


def detect_site(dest: Path, opts: Options) -> tuple[str, bool]:
    """(site folder, whether to add the starter page). An existing index.html at the root or in site/ is the site."""
    if opts.site:
        return opts.site, not (dest / opts.site / "index.html").exists()
    for candidate in (".", "site"):
        if (dest / candidate / "index.html").exists():
            return candidate, False
    return "site", True


# ------------------------------------------------------------------------------------------------------------- steps
def workflow_ref(platform: config.Platform, ctx: Context) -> str:
    return ctx.template_ref or str(platform.raw.get("workflow_ref") or "main")


def run_new_app(opts: Options, ctx: Context | None = None) -> tuple[int, Report]:
    ctx = ctx or Context()
    say, shell, report = ctx.say, ctx.shell, Report()
    started = ctx.clock()

    errors = validate(opts)
    if errors:
        for e in errors:
            say(f"fail  {e}")
        return 1, report
    platform = config.load(ctx.platform_file)
    owner, domain = platform.github_owner, platform.domain
    existing = find_app(platform, opts.name)
    dest = ctx.workspace / opts.name
    adopting = dest.exists() or existing is not None
    say(f"{'Adopting' if adopting else 'Creating'} {opts.app_type} app '{opts.name}' (host {opts.name}.{domain}, repo {owner}/{opts.name})")

    # 1. platform.yaml and the stg port
    used = {int(a["stg_port"]) for a in platform.apps if isinstance(a.get("stg_port"), int)}
    port = int(existing["stg_port"]) if existing and isinstance(existing.get("stg_port"), int) else config.next_free_port(used)
    backup = ctx.platform_file.read_text(encoding="utf-8")
    if existing is None:
        report.add("done", "platform.yaml", f"added {opts.name}: type {opts.app_type}, stg port {port}")
        add_app_entry(ctx.platform_file, entry_text(opts, port, owner, domain))
    else:
        report.add("skip", "platform.yaml", f"already lists {opts.name} (stg port {port})")

    # 2. the app folder from the template
    ref = workflow_ref(platform, ctx)
    site, placeholder = detect_site(dest, opts) if opts.app_type == "static" else ("site", False)
    flags = template.flags_for(opts.app_type, opts.auth, opts.db, placeholder)
    result = template.render(dest, opts.name, flags, owner, domain, ref, port, update=opts.update_template,
                             template_dir=template.template_dir_for(opts.app_type), dry_run=True, site=site)
    if result.created or result.updated:
        report.add("done", "template", f"{len(result.created)} file(s) created, {len(result.updated)} updated")
    else:
        report.add("skip", "template", f"all {len(result.same)} files already match")
    if result.differs:
        report.add("note", "template", f"{len(result.differs)} file(s) differ from the template and were left alone "
                                       f"(re-run with --update-template to overwrite): {', '.join(sorted(result.differs)[:8])}")


    # 3. the one set of confirmations (everything before this point is local and reversible)
    needs = []
    has_repo = shell.run(["gh", "repo", "view", f"{owner}/{opts.name}", "--json", "name"]).code == 0
    if not has_repo:
        needs.append(f"create the PUBLIC GitHub repository {owner}/{opts.name} and push the first commit")
    plan_text = _terraform_plan(shell, report)
    if plan_text:
        needs.append("apply Terraform: " + plan_text)
    if opts.db and opts.app_type != "static":
        needs.append("create the prod database and its three roles")
    if opts.auth and opts.app_type in ("web", "api", "ecommerce", "service"):
        needs.append("create the prod login client in the shared Keycloak")
    if needs and (opts.dry_run or not opts.yes):
        say("\nThis needs your confirmation, once:")
        for n in needs:
            say(f"  - {n}")
    if opts.dry_run:
        ctx.platform_file.write_text(backup, encoding="utf-8", newline="")
        _print(report, say)
        say("dry run: nothing was written (platform.yaml was put back); re-run with --yes to do it")
        return 0, report
    if needs and not opts.yes and not ctx.ask("Go ahead with all of it?"):
        ctx.platform_file.write_text(backup, encoding="utf-8", newline="")
        say("declined: platform.yaml was put back; nothing outward-facing happened.")
        return 2, report

    # the app folder: written only now
    template.render(dest, opts.name, flags, owner, domain, ref, port, update=opts.update_template,
                    template_dir=template.template_dir_for(opts.app_type), site=site)
    leftovers = template.leftover_tokens(dest)
    if leftovers:
        raise NewAppError(f"template bug: unreplaced placeholders in {', '.join(leftovers)}")
    if opts.app_type == "static":
        index = dest / site / "index.html"
        if index.exists() and ensure_footer_markers(index):
            report.add("done", "footer", f"placed the policy-links marker in {site}/index.html")

    # 4. policy pages
    if opts.app_type in ("web", "ecommerce", "service", "static"):
        res = shell.avk("legal", "render", opts.name)
        if res.code != 0:
            raise NewAppError("avk legal render failed:\n" + res.out[-400:])
        wrote = re.search(r"(\d+) file\(s\) written", res.out)
        if wrote and wrote.group(1) == "0":
            report.add("skip", "legal", "policy pages are up to date")
        else:
            report.add("done", "legal", "policy pages rendered (DRAFT banner, [TO COMPLETE] markers)")

    # 5. git, repository, protections
    _git(opts, dest, owner, report, shell, has_repo, ctx)

    # 6. DNS, tunnel route and email alias
    if plan_text:
        code = shell.avk("tf", "apply", "--yes").code
        if code != 0:
            raise NewAppError("terraform apply failed (see `avk tf plan`)")
        report.add("done", "terraform", plan_text)
    else:
        report.add("skip", "terraform", "no DNS, route or alias changes needed")

    # 7. database, secrets, login client, mail settings
    state = load_state(ctx, opts.name)
    if opts.db and opts.app_type != "static":
        if state.get("database"):
            report.add("skip", "database", "secrets and roles were created before")
        else:
            for env in ("stg", "prod"):
                _must(shell.avk("secrets", "init", opts.name, "--env", env), f"secrets init ({env})")
                _must(shell.avk("db", "create", opts.name, "--env", env), f"db create ({env})")
            mark_done(ctx, opts.name, "database")
            report.add("done", "database", "secrets and the three database roles exist for stg and prod")
    if opts.auth and opts.app_type in ("web", "api", "ecommerce", "service"):
        if state.get("login"):
            report.add("skip", "login", "Keycloak clients were created before")
        else:
            for env in ("stg", "prod"):
                _must(shell.avk("auth", "client", opts.name, "--env", env), f"auth client ({env})")
            mark_done(ctx, opts.name, "login")
            report.add("done", "login", "Keycloak clients exist for stg and prod")
    if opts.app_type in ("web", "ecommerce", "service"):
        if state.get("mail"):
            report.add("skip", "mail", "contact-form mail settings were written before")
        else:
            for env in ("stg", "prod"):
                _must(shell.avk("legal", "smtp", opts.name, "--env", env), f"mail settings ({env})")
            mark_done(ctx, opts.name, "mail")
            report.add("done", "mail", "contact-form mail settings written for stg and prod")

    # 8. stg (skipped when the stg copy is already running and nothing in the app folder changed)
    folder_changed = any(s == "done" and st in ("template", "git", "footer", "legal") for s, st, _ in report.lines)
    if stg_running(shell, opts.name) and not folder_changed:
        report.add("skip", "stg", f"already running at http://127.0.0.1:{port}/")
    else:
        up = shell.avk("stg", "up", opts.name, "--build", timeout=3000)
        if up.code != 0:
            raise NewAppError("avk stg up failed:\n" + up.out[-600:])
        report.add("done", "stg", f"http://127.0.0.1:{port}/")

    # 9. Wall
    wall = ctx.workspace / "wall" / "wall.py"
    if wall.exists():
        # `wall validate` judges every project in the workspace and exits non-zero when ANY folder lacks a manifest; only this app's
        # own line matters here.
        res = shell.run([sys.executable, str(wall), "validate"], cwd=ctx.workspace)
        mine = [line for line in res.out.splitlines() if line.split()[:2] and line.split()[1] == opts.name]
        if not any(line.split()[0] == "ok" for line in mine):
            raise NewAppError(f"wall validate does not accept {opts.name}:\n" + ("\n".join(mine) or res.out[-400:]))
        res = shell.run([sys.executable, str(wall), "check", opts.name], cwd=ctx.workspace)
        if res.code != 0:
            raise NewAppError(f"wall check {opts.name} failed:\n{res.out[-400:]}")
        report.add("ok", "wall", f"{opts.name} validates and its checks pass")

    minutes = (ctx.clock() - started) / 60
    _print(report, say)
    say(f"\nStg:  http://127.0.0.1:{port}/\nProd: https://{opts.name}.{domain} (after the first release is approved)\nRepo: https://github.com/{owner}/{opts.name}")
    todo = ["merge the first pull request (or approve the first release) and approve the `prod` deployment"]
    if opts.app_type in ("web", "ecommerce", "service", "static"):
        todo.append("complete the [TO COMPLETE] markers in the policy pages and have them reviewed (`legal.reviewed`)")
    todo.append("add a Better Stack monitor for the new host (RUNBOOK: Add a new app)")
    say("Left for you:\n" + "\n".join(f"  {i}. {t}" for i, t in enumerate(todo, 1)))
    say(f"\ncompleted in {minutes:.1f} minutes" if report.changed() else "\nnothing to do")
    return 0, report


def _must(res: Result, what: str) -> None:
    if res.code != 0:
        raise NewAppError(f"{what} failed:\n{res.out[-500:]}")


def _print(report: Report, say: Callable[[str], None]) -> None:
    for status, step, detail in report.lines:
        say(f"{status:5} {step}: {detail}")


def _terraform_plan(shell: Shell, report: Report) -> str:
    """Run `avk tf plan` and summarise it. Empty string = no changes."""
    res = shell.avk("tf", "plan")
    if res.code != 0:
        raise NewAppError("terraform plan failed:\n" + res.out[-600:])
    if "no changes" in res.out.lower():
        return ""
    m = re.search(r"Plan:\s*(\d+) to add,\s*(\d+) to change,\s*(\d+) to destroy", res.out)
    summary = m.group(0) if m else "Terraform reports changes (see `avk tf plan`)"
    resources = re.findall(r"#\s+(\S+)\s+will be (created|updated in-place|destroyed)", res.out)
    detail = ", ".join(f"{addr} {what}" for addr, what in resources[:6])
    return f"{summary}" + (f" ({detail})" if detail else "")


def _ensure_identity(shell: Shell, dest: Path) -> None:
    """A new repository on this PC has no git identity (there is no global one): copy the platform repository's, locally."""
    for key in ("user.name", "user.email"):
        if shell.run(["git", "config", "--get", key], cwd=dest).out.strip():
            continue
        value = shell.run(["git", "config", "--get", key], cwd=config.REPO_ROOT).out.strip()
        if not value:
            raise NewAppError(f"git has no {key}: run `git config --global {key} <value>` once, then run this again")
        _must(shell.run(["git", "config", key, value], cwd=dest), f"git config {key}")


def _git(opts: Options, dest: Path, owner: str, report: Report, shell: Shell, has_repo: bool, ctx: Context) -> None:
    slug = f"{owner}/{opts.name}"
    fresh = not (dest / ".git").exists()
    if fresh:
        _must(shell.run(["git", "init", "-b", "main"], cwd=dest), "git init")
    _ensure_identity(shell, dest)
    status = shell.run(["git", "status", "--porcelain"], cwd=dest).out.strip()
    if fresh:
        branch = "main"
    else:
        probe = shell.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=dest)
        branch = probe.out.strip() if probe.code == 0 else "main"       # a repository with no commit yet (an earlier run stopped early)
        if status and branch == "main":
            _must(shell.run(["git", "checkout", "-b", ADOPT_BRANCH], cwd=dest), "git checkout")
            branch = ADOPT_BRANCH
    if status:
        _must(shell.run(["git", "add", "-A"], cwd=dest), "git add")
        if (dest / "mvnw").exists():                    # Windows does not store the executable bit; the pipeline runs ./mvnw
            _must(shell.run(["git", "update-index", "--chmod=+x", "mvnw"], cwd=dest), "git chmod mvnw")
        message = f"Onboard {opts.name} to the AVK platform\n\n{ATTRIBUTION}"
        _must(shell.run(["git", "commit", "-m", message], cwd=dest), "git commit")
        report.add("done", "git", f"committed on {branch}")
    else:
        report.add("skip", "git", "nothing to commit")
    if not has_repo:
        _must(shell.run(["gh", "repo", "create", slug, "--public", "--source", ".", "--remote", "origin", "--push"], cwd=dest), "gh repo create")
        report.add("done", "repo", f"created https://github.com/{slug} and pushed main")
    elif branch != "main" and status:
        _must(shell.run(["git", "push", "-u", "origin", branch], cwd=dest), "git push")
        pr = shell.run(["gh", "pr", "create", "--repo", slug, "--head", branch, "--title", f"Onboard {opts.name} to the AVK platform",
                        "--body", f"Adds the pipeline, Dockerfile, contract and policy pages.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"],
                       cwd=dest)
        report.add("done", "repo", "pushed the onboarding branch and opened a pull request" if pr.code == 0 else f"pushed {branch}; open the pull request by hand")
    else:
        report.add("skip", "repo", f"{slug} exists and the branch is already pushed")
    if load_state(ctx, opts.name).get("repo-settings"):
        report.add("skip", "repo settings", "applied before")
        return
    ps1 = config.REPO_ROOT / "tools" / "repo-settings.ps1"
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Repo", slug, "-RequiredChecks", REPO_SETTINGS_CHECKS, "-ProdEnvironment"]
    res = shell.run(args)
    if res.code == 0:
        mark_done(ctx, opts.name, "repo-settings")
    report.add("done" if res.code == 0 else "note", "repo settings", "branch protection, prod approval, scanning" if res.code == 0 else "repo-settings.ps1 reported problems:\n" + res.out[-300:])


# ------------------------------------------------------------------------------------------------------- retire-app
def run_retire_app(name: str, typed: str | None, ctx: Context | None = None) -> int:
    ctx = ctx or Context()
    say, shell = ctx.say, ctx.shell
    platform = config.load(ctx.platform_file)
    app = find_app(platform, name)
    if app is None:
        say(f"fail  '{name}' is not in platform.yaml")
        return 1
    owner = platform.github_owner
    slug = str(app.get("repo") or f"{owner}/{name}")
    steps = [
        "take a final backup of every prod database (`avk backup run`)",
        f"stop and remove the prod containers {name}-blue and {name}-green and their Traefik route",
        f"stop and remove the stg container {name}-stg",
        "delete the Keycloak clients (stg and prod)" if app.get("type") != "static" else None,
        f"set status: retired in platform.yaml and apply Terraform: removes the DNS record, tunnel route and the support-{name}@ alias",
        f"ARCHIVE the GitHub repository {slug} (it is never deleted)",
    ]
    keeps = [f"the databases and secret files of {name} are KEPT (delete them by hand after the backup retention window)",
             f"the folder {ctx.workspace / name} is KEPT"]
    say(f"retire-app {name} would:")
    for s in steps:
        if s:
            say(f"  - {s}")
    for k in keeps:
        say(f"  (kept) {k}")
    if app.get("status") == "retired":
        say("already retired: nothing to do")
        return 0
    phrase = f"retire {name}"
    if typed != phrase:
        say(f'\nfail  this is destructive. Re-run with --confirm "{phrase}" to go ahead.')
        return 1
    for what, argv in (("final backup", ["backup", "run"]),):
        if shell.avk(*argv).code != 0:
            say(f"fail  {what} failed: nothing was removed")
            return 1
    for container in (f"{name}-blue", f"{name}-green", f"{name}-stg"):
        shell.run(["docker", "rm", "-f", container])
    if app.get("type") != "static":
        for env in ("stg", "prod"):
            shell.avk("auth", "client-delete", name, "--env", env)
    set_app_status(ctx.platform_file, name, "retired")
    if shell.avk("tf", "plan").code != 0 or shell.avk("tf", "apply", "--yes").code != 0:
        say("fail  terraform did not apply: platform.yaml says retired; run `avk tf plan` and apply to remove the DNS record")
        return 1
    shell.run(["gh", "repo", "archive", slug, "--yes"])
    say(f"ok    {name} retired. Remove its Better Stack monitor and, when the backup window has passed, its databases and secrets.")
    return 0


def main_new(args) -> int:
    opts = Options(name=args.name, app_type=args.type, auth=not args.no_auth, db=not args.no_db, update_template=args.update_template,
                   yes=args.yes, dry_run=args.dry_run, site=args.site)
    try:
        code, _ = run_new_app(opts)
    except NewAppError as exc:
        print(f"fail  {exc}")
        return 1
    return code


def main_retire(args) -> int:
    return run_retire_app(args.name, args.confirm)
