"""Track the git repositories cloned on this device, and the AI configuration in them.

For each repository it records:

    path         where it is, relative to the home folder (e.g. ~/Projects/app)
    origin       remote "origin" as host/owner/repo; credentials, query and
                 fragment are removed
    cloned_at    when it was cloned (or created, if it was not cloned), from the
                 first entry of git's reflog
    last_active  the latest reflog entry: commit, checkout, pull, ...
    ai           AI agent files present (names only), Claude Code rule counts and
                 MCP server names from the repo's own config files

It never reads source files, commit contents, author names or e-mails. Agent
files (CLAUDE.md, AGENTS.md, .mcp.json, ...) are read only to compute a SHA-256
fingerprint per agent setup; their text is never stored or returned. Repositories are searched in folders that Claude Code, Cursor and VS Code
already know, plus common development folders, a few levels deep.

Each scan is saved to a local history file so the next scan can show new
clones, repositories that were not found again, and changes.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

COMMON_FOLDERS = ("Projects", "projects", "code", "Code", "src", "dev", "Developer", "repos", "git", "GitHub",
                  "Documents/GitHub", "workspace", "Workspace", "work", "Sites")
SKIP_FOLDERS = {"node_modules", "Library", "venv", ".venv", "dist", "build", "target", "vendor", "__pycache__",
                ".cache", ".Trash", "Applications", "Pictures", "Music", "Movies"}
MAX_DEPTH = 4
MAX_FOLDERS_VISITED = 20000
HISTORY_LIMIT = 50

# Agent files that may exist in a repository. Only their presence is recorded.
AI_FILES = {
    "CLAUDE.md": "Claude Code instructions",
    "CLAUDE.local.md": "Claude Code personal instructions",
    "AGENTS.md": "Agent instructions (AGENTS.md)",
    "GEMINI.md": "Gemini CLI instructions",
    ".cursorrules": "Cursor rules (legacy)",
    ".windsurfrules": "Windsurf rules",
    ".github/copilot-instructions.md": "Copilot instructions",
    ".aider.conf.yml": "Aider config",
    ".continue/config.yaml": "Continue config",
    ".continue/config.json": "Continue config",
    ".gemini/settings.json": "Gemini CLI settings",
}
# Folders whose entries are counted, never read.
AI_FOLDERS = {
    ".claude/agents": "Claude Code subagents",
    ".claude/commands": "Claude Code commands",
    ".cursor/rules": "Cursor rules",
    ".github/instructions": "Copilot instruction files",
}

# Which agent each configuration file belongs to. A repository's "agent setup"
# for a tool is the set of these files that exist; its fingerprint is a SHA-256
# over their names and contents. Personal *.local.* files are left out, so the
# same commit gives the same fingerprint on every machine.
AGENT_SETUPS = {
    "claude-code": {"label": "Claude Code", "provider": "anthropic",
                    "paths": ["CLAUDE.md", ".claude/settings.json", ".claude/agents", ".claude/commands", ".mcp.json"]},
    "cursor": {"label": "Cursor", "provider": "cursor",
               "paths": [".cursorrules", ".cursor/rules", ".cursor/mcp.json"]},
    "github-copilot": {"label": "GitHub Copilot", "provider": "github",
                       "paths": [".github/copilot-instructions.md", ".github/instructions", ".vscode/mcp.json"]},
    "gemini-cli": {"label": "Gemini CLI", "provider": "google", "paths": ["GEMINI.md", ".gemini/settings.json"]},
    "agents-md": {"label": "AGENTS.md agents (Codex and others)", "provider": "unspecified", "paths": ["AGENTS.md"]},
    "aider": {"label": "Aider", "provider": "unspecified", "paths": [".aider.conf.yml"]},
    "continue": {"label": "Continue", "provider": "unspecified", "paths": [".continue/config.yaml", ".continue/config.json"]},
    "windsurf": {"label": "Windsurf", "provider": "unspecified", "paths": [".windsurfrules"]},
}

_SCP_REMOTE = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):(?!//)(.+)$")
_PLAIN_WORD = re.compile(r"^[A-Za-z][A-Za-z-]{0,40}$")


def _is_unc(path: Path) -> bool:
    return str(path).startswith(("\\\\", "//"))


# ── finding repositories ───────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def editor_state_dbs(home: Path, platform: str) -> list:
    if platform == "darwin":
        base = home / "Library" / "Application Support"
    elif platform == "win32":
        base = home / "AppData" / "Roaming"
    else:
        base = home / ".config"
    return [base / app / "User" / "globalStorage" / "state.vscdb" for app in ("Code", "Cursor", "Code - Insiders")]


def _recent_editor_folders(db: Path) -> list:
    """Local folders from VS Code/Cursor's "recently opened" list."""
    if not db.is_file():
        return []
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as connection:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = 'history.recentlyOpenedPathsList'").fetchone()
    except sqlite3.Error:
        return []
    try:
        entries = json.loads(row[0]).get("entries", []) if row else []
    except (ValueError, AttributeError, TypeError):
        return []
    folders = []
    for entry in entries if isinstance(entries, list) else []:
        uri = entry.get("folderUri") if isinstance(entry, dict) else None
        if isinstance(uri, str) and uri.startswith("file://"):
            parts = urlsplit(uri)
            if parts.netloc and parts.netloc.lower() != "localhost":
                continue
            path = unquote(parts.path)
            if sys.platform == "win32" and re.match(r"^/[A-Za-z]:", path):
                path = path[1:]
            folders.append(Path(path))
    return folders


def known_folders(home: Path, platform: str) -> list:
    """Folders that Claude Code, Cursor and VS Code have opened."""
    folders = [Path(project) for project in (_read_json(home / ".claude.json").get("projects") or {})
               if isinstance(project, str) and os.path.isabs(project)]
    for db in editor_state_dbs(home, platform):
        folders += _recent_editor_folders(db)
    return folders


def _git_dir(folder: Path):
    """The .git folder of a repository (following "gitdir:" files of worktrees), or None."""
    if _is_unc(folder):
        return None
    dot_git = folder / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            line = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        if line.startswith("gitdir:"):
            target = Path(line[len("gitdir:"):].strip())
            target = target if target.is_absolute() else (folder / target)
            return target if not _is_unc(target) and target.is_dir() else None
    return None


def _repo_root(folder: Path, home: Path):
    """The repository containing folder (folder itself or a parent up to home)."""
    current = folder
    while True:
        if _git_dir(current):
            return current
        if current == home or current.parent == current:
            return None
        current = current.parent


def find_repositories(home: Path, platform: str = sys.platform, roots=None, known=None) -> list:
    """Repository folders in known folders and (a few levels deep) common development folders."""
    found, identities = [], set()

    def add(folder: Path):
        # Compare the folder itself, not its name: on case-insensitive disks
        # ~/Projects and ~/projects are the same folder.
        try:
            folder = folder.resolve()
            if _is_unc(folder):
                return
            stat = folder.stat()
        except OSError:
            return
        if (stat.st_dev, stat.st_ino) not in identities:
            identities.add((stat.st_dev, stat.st_ino))
            found.append(folder)

    for folder in (known if known is not None else known_folders(home, platform)):
        if not _is_unc(folder) and folder.is_dir():
            root = _repo_root(folder.resolve(), home)
            if root:
                add(root)

    visited = 0
    queue = [(Path(root), 0) for root in (roots if roots is not None else [home / name for name in COMMON_FOLDERS])]
    while queue and visited < MAX_FOLDERS_VISITED:
        folder, depth = queue.pop(0)
        visited += 1
        if _is_unc(folder):
            continue
        if _git_dir(folder):
            add(folder)
            continue  # do not look for repositories inside repositories
        if depth >= MAX_DEPTH:
            continue
        try:
            with os.scandir(folder) as entries:
                children = sorted(entry.name for entry in entries
                                  if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".")
                                  and entry.name not in SKIP_FOLDERS)
        except OSError:
            continue
        queue += [(folder / child, depth + 1) for child in children]
    return found


# ── describing one repository ──────────────────────────────────────────────────

def normalize_remote(url: str):
    """host/owner/repo for a remote URL, without credentials; "local" for a path; None if unknown."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" in url:
        parts = urlsplit(url)
        if parts.scheme == "file":
            return "local"
        host = parts.hostname or ""
        path = parts.path
    else:
        scp = _SCP_REMOTE.match(url)
        if not scp or os.path.isabs(url) or re.match(r"^[A-Za-z]:\\", url):
            return "local"
        host, path = scp.group(1), scp.group(2)
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path}" if host and path else None


def _origin(git_dir: Path):
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read(git_dir / "config", encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return None, 0
    remotes = [section for section in parser.sections() if section.startswith('remote "')]
    url = parser.get('remote "origin"', "url", fallback=None)
    if url is None and remotes:
        url = parser.get(remotes[0], "url", fallback=None)
    return normalize_remote(url), len(remotes)


def _reflog_entry(line: str):
    """(timestamp, action) from one reflog line; author name, e-mail and message text are dropped."""
    meta, _, message = line.partition("\t")
    fields = meta.split()
    if len(fields) < 2 or not fields[-2].isdigit():
        return None
    action = message.split(":", 1)[0].strip().split(" ")[0] if message else ""
    when = datetime.fromtimestamp(int(fields[-2]), tz=timezone.utc).replace(microsecond=0).isoformat()
    return when, action if _PLAIN_WORD.match(action or "") else "other"


def _reflog_ends(git_dir: Path):
    """First and last reflog entries, reading only the start and the end of the file."""
    log = git_dir / "logs" / "HEAD"
    try:
        with open(log, "rb") as handle:
            first = handle.readline().decode("utf-8", errors="replace")
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            tail = handle.read().decode("utf-8", errors="replace").strip().splitlines()
    except OSError:
        return None, None
    return _reflog_entry(first), (_reflog_entry(tail[-1]) if tail else None)


def ai_configuration(repo: Path) -> dict:
    """AI agent files in a repository: presence, counts and names, never their text."""
    files = sorted(name for name in AI_FILES if (repo / name).is_file())
    folders = {}
    for name in AI_FOLDERS:
        try:
            count = sum(1 for entry in os.scandir(repo / name) if entry.is_file() and not entry.name.startswith("."))
        except OSError:
            continue
        if count:
            folders[name] = count
    config = {}
    if files:
        config["files"] = files
    if folders:
        config["folders"] = folders
    mcp = []
    for name in (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json"):
        data = _read_json(repo / name)
        servers = data.get("mcpServers") or data.get("servers")
        if isinstance(servers, dict):
            mcp += [{"config": name, "server": server} for server in sorted(servers) if isinstance(server, str)]
    if mcp:
        config["mcp_servers"] = mcp
    rules = []
    for name in (".claude/settings.json", ".claude/settings.local.json"):
        if not (repo / name).is_file():
            continue
        permissions = _read_json(repo / name).get("permissions")
        permissions = permissions if isinstance(permissions, dict) else {}
        mode = permissions.get("defaultMode")
        rules.append({"config": name,
                      "mode": mode if isinstance(mode, str) and _PLAIN_WORD.match(mode) else "default",
                      **{key: len(permissions[key]) if isinstance(permissions.get(key), list) else 0
                         for key in ("allow", "ask", "deny")}})
    if rules:
        config["claude_rules"] = rules
    return config


def _setup_files(repo: Path, paths) -> list:
    """Existing files of an agent setup, as sorted repo-relative paths (folders expanded)."""
    files = []
    for relative in paths:
        target = repo / relative
        if target.is_file():
            files.append(relative)
        elif target.is_dir():
            try:
                files += [f"{relative}/{entry.name}" for entry in os.scandir(target)
                          if entry.is_file() and not entry.name.startswith(".")]
            except OSError:
                continue
    return sorted(name for name in files if ".local." not in name.rsplit("/", 1)[-1])


def setup_fingerprint(repo: Path, files) -> str | None:
    """SHA-256 over the names and contents of an agent setup's files.

    The contents are read only to compute this hash; they are never stored or returned.
    """
    combined = hashlib.sha256()
    for relative in sorted(files):
        file_hash = hashlib.sha256()
        try:
            with open(repo / relative, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 16), b""):
                    file_hash.update(chunk)
        except OSError:
            return None
        combined.update(relative.encode("utf-8") + b"\0" + file_hash.hexdigest().encode("ascii") + b"\n")
    return combined.hexdigest()


def agent_setups(repo: Path) -> list:
    """Per agent tool: its configuration files in this repository and their fingerprint."""
    setups = []
    for tool, spec in AGENT_SETUPS.items():
        files = _setup_files(repo, spec["paths"])
        if files:
            setups.append({"tool": tool, "label": spec["label"], "provider": spec["provider"], "files": files,
                           "fingerprint": setup_fingerprint(repo, files)})
    return setups


def display_path(path: Path, home: Path) -> str:
    """Path relative to home ("~/..."), also when one of them is reached through a link."""
    for base in (home, home.resolve()):
        if path == base:
            return "~"
        try:
            return "~/" + path.relative_to(base).as_posix()
        except ValueError:
            continue
    return str(path)


def describe_repository(repo: Path, home: Path) -> dict:
    git_dir = _git_dir(repo)
    origin, remotes = _origin(git_dir)
    first, last = _reflog_ends(git_dir)
    info = {"path": display_path(repo, home), "origin": origin, "remotes": remotes}
    if first:
        info["cloned_at"], action = first
        info["how"] = "cloned" if action == "clone" else "created here"
    if last:
        info["last_active"], info["last_action"] = last
    if not first:
        try:
            created = datetime.fromtimestamp(git_dir.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
            info["last_active"] = created.isoformat()
            info["how"] = "unknown (no reflog)"
        except OSError:
            pass
    info["ai"] = ai_configuration(repo)
    info["agents"] = agent_setups(repo)
    return info


def resolve_display_path(path: str, home: Path) -> Path:
    """Inverse of display_path for paths shown as "~/...". """
    if path == "~":
        return home
    if path.startswith(("~/", "~" + os.sep)):
        return home / path[2:]
    return Path(path)


# ── history between scans ──────────────────────────────────────────────────────

def _comparable(repo: dict) -> dict:
    compared = {key: repo.get(key) for key in ("origin", "last_active", "ai")}
    # Agent file contents, through their fingerprints: an edited CLAUDE.md counts as a change.
    compared["agents"] = {setup["tool"]: setup["fingerprint"] for setup in repo.get("agents") or []}
    return compared


def compare(previous: list, current: list) -> dict:
    before = {repo["path"]: repo for repo in previous}
    now = {repo["path"]: repo for repo in current}
    changed = []
    for path in sorted(set(before) & set(now)):
        old, new = _comparable(before[path]), _comparable(now[path])
        fields = [key for key in old if old[key] != new[key]]
        if fields:
            changed.append({"path": path, "fields": fields})
    return {"new": sorted(set(now) - set(before)), "not_found": sorted(set(before) - set(now)), "changed": changed}


def load_history(path: Path) -> dict:
    data = _read_json(path)
    scans = data.get("scans")
    return {"scans": scans if isinstance(scans, list) else []}


def save_history(path: Path, history: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        json.dump(history, handle, indent=2)
        temporary = handle.name
    os.replace(temporary, path)  # atomic: a crash never leaves a half-written history


def scan_repositories(history_path: Path, home=None, platform=None, roots=None, known=None, now=None) -> dict:
    """Find and describe repositories, compare with the previous scan, and save this one."""
    home = Path(home) if home is not None else Path.home()
    platform = platform or sys.platform
    repos = sorted((describe_repository(repo, home) for repo in find_repositories(home, platform, roots, known)),
                   key=lambda repo: repo.get("last_active") or "", reverse=True)
    scanned_at = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    history = load_history(history_path)
    previous = history["scans"][-1] if history["scans"] else None
    changes = compare(previous["repos"], repos) if previous else None
    history["scans"] = (history["scans"] + [{"scanned_at": scanned_at, "repos": repos}])[-HISTORY_LIMIT:]
    save_history(history_path, history)
    return {"scanned_at": scanned_at, "repos": repos,
            "previous_scan_at": previous["scanned_at"] if previous else None,
            "changes": changes, "scans_in_history": len(history["scans"])}


if __name__ == "__main__":
    home = Path.home()
    print(json.dumps({"repository_count": len(find_repositories(home))}))
