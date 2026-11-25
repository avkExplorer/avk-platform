"""Render `app-template/spring/` into a new app folder.

The template is a copy of the tested pilot (`hello`) with three kinds of placeholders:
- tokens such as `__APP__`, `__PKG__`, `__CLASS__` in file names and file contents;
- block markers `[[if web]] ... [[end]]` on lines of their own (inside a comment of the file's own language), kept or dropped by the
  app's flags (type web/api, auth on/off, database on/off);
- file rules in `template.yaml`: a path pattern and a `when` condition; a file whose condition is false is not created.

Rendering never overwrites: with an existing folder it only reports files that are missing or differ (the "adopt" mode of `avk new-app`),
and writes only missing files, or differing ones when `update=True`.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from avk import config

TEMPLATE_ROOT = config.REPO_ROOT / "app-template"
TEMPLATE_DIR = TEMPLATE_ROOT / "spring"                          # the default; `template_dir_for` picks by app type


def template_dir_for(app_type: str) -> Path:
    """Spring Boot for web/api (and the ecommerce/service variants of web), nginx for static sites."""
    return TEMPLATE_ROOT / ("static" if app_type == "static" else "spring")
MARKER = re.compile(r"\[\[(if|end)(?:\s+([^\]]*))?\]\]")
COND_WORDS = {"and", "or", "not", "(", ")"}
TEXT_SUFFIXES = {".java", ".xml", ".yml", ".yaml", ".json", ".properties", ".md", ".txt", ".sql", ".cmd", ".ts", ".js", ".html", ".css",
                 ".csv", ".conf", ".ps1", ".gitignore", ".gitattributes", ".dockerignore", ""}


class TemplateError(ValueError):
    pass


def evaluate(condition: str, flags: dict[str, bool]) -> bool:
    """`web and auth`, `not db`, `(api or web) and db`. Only flag names and and/or/not/parentheses are allowed."""
    cleaned = condition.replace("(", " ( ").replace(")", " ) ").split()
    for word in cleaned:
        if word not in COND_WORDS and word not in flags:
            raise TemplateError(f"unknown word {word!r} in condition {condition!r} (flags: {sorted(flags)})")
    return bool(eval(" ".join(cleaned), {"__builtins__": {}}, dict(flags)))  # noqa: S307 - words were checked against a whitelist above


def apply_markers(text: str, flags: dict[str, bool], where: str = "") -> str:
    """Keep or drop `[[if cond]]` ... `[[end]]` blocks; the marker lines themselves never reach the output."""
    out: list[str] = []
    stack: list[bool] = []
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        m = MARKER.search(line)
        if m:
            if m.group(1) == "if":
                if not m.group(2):
                    raise TemplateError(f"{where}:{number}: [[if]] needs a condition")
                stack.append(evaluate(m.group(2), flags))
            else:
                if not stack:
                    raise TemplateError(f"{where}:{number}: [[end]] without [[if]]")
                stack.pop()
            continue
        if all(stack):
            out.append(line)
    if stack:
        raise TemplateError(f"{where}: [[if]] block is never closed")
    return "".join(out)


def flags_for(app_type: str, auth: bool = True, db: bool = True, placeholder: bool = True) -> dict[str, bool]:
    """The flags every template condition is evaluated with. `static` apps have no code, database or login. `placeholder` adds a starter
    page to a new static site (an adopted site has its own pages)."""
    static = app_type == "static"
    return {"web": app_type in ("web", "ecommerce", "service"), "api": app_type == "api", "static": static,
            "auth": auth and not static, "noauth": not auth or static, "db": db and not static, "nodb": not db or static,
            "placeholder": placeholder}


def tokens_for(name: str, owner: str, domain: str, platform_ref: str, stg_port: int, site: str = "site") -> dict[str, str]:
    pkg = re.sub(r"[^a-z0-9]", "", name.lower())
    if not pkg or pkg[0].isdigit():
        pkg = "app" + pkg
    cls = "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", name) if part) or "App"
    ticket = (re.sub(r"[^A-Za-z]", "", name).upper() + "TKT")[:3]
    # A caller that names the reusable workflow by branch (`@main`) is signed with the identity `refs/heads/main`; by a commit SHA, with the SHA.
    id_ref = "refs/heads/main" if platform_ref == "main" else platform_ref
    return {"__APP__": name, "__PKG__": pkg, "__CLASS__": cls, "__OWNER__": owner, "__DOMAIN__": domain, "__TICKET__": ticket,
            "__HOST__": f"{name}.{domain}", "__PLATFORM_REF__": platform_ref, "__PLATFORM_ID_REF__": id_ref, "__STG_PORT__": str(stg_port),
            "__SITE__": site}


def substitute(text: str, tokens: dict[str, str]) -> str:
    for token in sorted(tokens, key=len, reverse=True):
        text = text.replace(token, tokens[token])
    return text


def load_rules(template_dir: Path = TEMPLATE_DIR) -> list[dict]:
    path = template_dir / "template.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("files") or [])


def wanted(relative: str, rules: list[dict], flags: dict[str, bool]) -> bool:
    for rule in rules:
        if fnmatch.fnmatch(relative, rule["path"]):
            return evaluate(rule["when"], flags)
    return True


@dataclass
class Result:
    created: list[str] = field(default_factory=list)
    same: list[str] = field(default_factory=list)
    differs: list[str] = field(default_factory=list)          # exists but differs: reported, not overwritten (unless update)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)          # not wanted for these flags

    def changed(self) -> bool:
        return bool(self.created or self.updated)


def split_variant(relative: str) -> tuple[str, list[str]]:
    """`SecurityConfig.java@api-auth` -> (`SecurityConfig.java`, [`api`, `auth`]): the file exists only when ALL the named flags are true.
    Several variants of one file share a destination name and differ in the part after the last `@`."""
    head, sep, tail = relative.rpartition("@")
    if not sep or "/" in tail:
        return relative, []
    return head, [word for word in tail.split("-") if word]


def planned_files(template_dir: Path, flags: dict[str, bool], tokens: dict[str, str]) -> dict[str, tuple[Path, bool]]:
    """destination path (relative, tokens substituted) -> (template file, wanted)."""
    rules = load_rules(template_dir)
    plan: dict[str, tuple[Path, bool]] = {}
    for source in sorted(p for p in template_dir.rglob("*") if p.is_file() and p.name != "template.yaml"):
        relative = source.relative_to(template_dir).as_posix()
        destination, needs = split_variant(relative)
        unknown = [w for w in needs if w not in flags]
        if unknown:
            raise TemplateError(f"{relative}: unknown flag(s) {unknown} in the file name (flags: {sorted(flags)})")
        is_wanted = wanted(relative, rules, flags) and all(flags[w] for w in needs)
        key = substitute(destination, tokens)
        if key in plan and plan[key][1] and is_wanted:
            raise TemplateError(f"two template files want to become {key}")
        if key not in plan or is_wanted:
            plan[key] = (source, is_wanted)
    return plan


def render_file(source: Path, flags: dict[str, bool], tokens: dict[str, str], relative: str) -> bytes:
    raw = source.read_bytes()
    name = Path(split_variant(relative)[0]).name                  # `X.java@db` is a Java file: judge by the destination name
    if Path(name).suffix.lower() not in TEXT_SUFFIXES and name not in (".gitignore", ".gitattributes", ".dockerignore"):
        return raw                                                # binary (for example the Maven wrapper jar): copied as is
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return substitute(apply_markers(text, flags, relative), tokens).encode("utf-8")


def render(dest: Path, name: str, flags: dict[str, bool], owner: str, domain: str, platform_ref: str, stg_port: int,
           update: bool = False, template_dir: Path = TEMPLATE_DIR, dry_run: bool = False, site: str = "site") -> Result:
    """Create the app folder from the template, or (folder exists) report what is missing or different and add what is missing."""
    tokens = tokens_for(name, owner, domain, platform_ref, stg_port, site)
    result = Result()
    for relative, (source, is_wanted) in planned_files(template_dir, flags, tokens).items():
        if not is_wanted:
            result.skipped.append(relative)
            continue
        content = render_file(source, flags, tokens, relative)
        target = dest / relative
        if not target.exists():
            result.created.append(relative)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        elif target.read_bytes().replace(b"\r\n", b"\n") == content.replace(b"\r\n", b"\n"):
            result.same.append(relative)
        elif update:
            result.updated.append(relative)
            if not dry_run:
                target.write_bytes(content)
        else:
            result.differs.append(relative)
    return result


def leftover_tokens(dest: Path) -> list[str]:
    """Files that still contain a `__TOKEN__` or a block marker after rendering (a template bug)."""
    bad = []
    pattern = re.compile(r"__(APP|PKG|CLASS|OWNER|DOMAIN|TICKET|HOST|PLATFORM_ID_REF|PLATFORM_REF|STG_PORT|SITE)__|\[\[(if|end)\b")
    for path in dest.rglob("*"):
        if path.is_file() and ".git" not in path.parts and "node_modules" not in path.parts and path.suffix.lower() in TEXT_SUFFIXES:
            try:
                if pattern.search(path.read_text(encoding="utf-8")):
                    bad.append(path.relative_to(dest).as_posix())
            except UnicodeDecodeError:
                continue
    return bad
