"""Read-only device discovery for Footprints.

Collects identifiers, never contents, and returns evidence items in the format
that discovery.js matches (see docs/evidence-format.md):

    {"kind": "process", "value": "claude", "app": "com.anthropic.claude-code",
     "parent": "com.anthropic.claudefordesktop", "count": 5}

What is read (the complete allowlist):

    process            process ID, parent ID and executable name/path from the OS
                       process list (never arguments, environment or open files)
    app                CFBundleIdentifier/CFBundleShortVersionString from Info.plist
                       of apps in /Applications and ~/Applications (macOS only)
    editor-extension   folder NAMES in ~/.vscode, ~/.vscode-insiders, ~/.cursor,
                       ~/.vscode-oss and ~/.windsurf extension folders
    browser-extension  folder NAMES under <Chromium browser>/<profile>/Extensions
    native-host        file NAMES in the browsers' NativeMessagingHosts folders

Permissions (names only), attached to the item they belong to or as own kinds:

    app permissions    sandbox on/off and entitlement NAMES from each app's code
                       signature (`codesign`), macOS only
    granted            macOS privacy grants (service + bundle ID) from the TCC
                       databases, only if macOS allows reading them (it requires
                       Full Disk Access; otherwise reported as not collected)
    extension perms    `permissions`, `host_permissions` and content-script match
                       patterns from the newest manifest.json of each browser
                       extension, reduced to names and "all sites"/"some sites"
    mcp-server         server NAMES and transport from MCP config files; never
                       commands, arguments, environment, URLs or project paths
    agent-rules        Claude Code permission mode and rule COUNTS; never rules

Nothing is written, nothing is sent over the network, and the output contains no
paths: only names, IDs, versions, browser and profile folder names, and counts.
"""

from __future__ import annotations

import csv
import io
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote

EDITOR_DIRS = {
    "vscode": ".vscode/extensions",
    "vscode-insiders": ".vscode-insiders/extensions",
    "cursor": ".cursor/extensions",
    "vscodium": ".vscode-oss/extensions",
    "windsurf": ".windsurf/extensions",
}

CHROMIUM_ROOTS = {
    "darwin": {
        "chrome": "Library/Application Support/Google/Chrome",
        "edge": "Library/Application Support/Microsoft Edge",
        "brave": "Library/Application Support/BraveSoftware/Brave-Browser",
        "arc": "Library/Application Support/Arc/User Data",
        "chromium": "Library/Application Support/Chromium",
    },
    "linux": {
        "chrome": ".config/google-chrome",
        "edge": ".config/microsoft-edge",
        "brave": ".config/BraveSoftware/Brave-Browser",
        "chromium": ".config/chromium",
    },
    "win32": {
        "chrome": "AppData/Local/Google/Chrome/User Data",
        "edge": "AppData/Local/Microsoft/Edge/User Data",
        "brave": "AppData/Local/BraveSoftware/Brave-Browser/User Data",
        "chromium": "AppData/Local/Chromium/User Data",
    },
}

TCC_SERVICES = {
    "kTCCServiceAccessibility": "accessibility",
    "kTCCServiceSystemPolicyAllFiles": "full-disk-access",
    "kTCCServiceScreenCapture": "screen-recording",
    "kTCCServiceListenEvent": "input-monitoring",
    "kTCCServicePostEvent": "control-input",
    "kTCCServiceAppleEvents": "automation",
    "kTCCServiceCamera": "camera",
    "kTCCServiceMicrophone": "microphone",
    "kTCCServiceAddressBook": "contacts",
    "kTCCServiceCalendar": "calendars",
    "kTCCServiceReminders": "reminders",
    "kTCCServicePhotos": "photos",
    "kTCCServiceSystemPolicyDocumentsFolder": "documents-folder",
    "kTCCServiceSystemPolicyDesktopFolder": "desktop-folder",
    "kTCCServiceSystemPolicyDownloadsFolder": "downloads-folder",
    "kTCCServiceSystemPolicyRemovableVolumes": "removable-volumes",
    "kTCCServiceSystemPolicyNetworkVolumes": "network-volumes",
}

CLAUDE_DESKTOP_CONFIG = {
    "darwin": "Library/Application Support/Claude/claude_desktop_config.json",
    "win32": "AppData/Roaming/Claude/claude_desktop_config.json",
}
CLAUDE_CODE_MANAGED_SETTINGS = {
    "darwin": Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    "linux": Path("/etc/claude-code/managed-settings.json"),
    "win32": Path("C:/ProgramData/ClaudeCode/managed-settings.json"),
}
MCP_TRANSPORTS = {"stdio", "http", "sse", "streamable-http"}
SYSTEM_PREFIXES = ("/System/", "/usr/lib/", "/usr/libexec/", "/usr/sbin/", "/usr/bin/", "/bin/", "/sbin/", "/Library/Apple/")

_EXTENSION_FOLDER = re.compile(r"^(?P<id>[^.\s]+\.[^\s]+?)-(?P<version>\d+(?:\.\d+){1,3})(?:-[\w-]+)?$")
_CHROME_EXTENSION_ID = re.compile(r"^[a-p]{32}$")
_BUNDLE_PATH = re.compile(r"^(.*?\.app)/Contents/")
_ALL_SITES = re.compile(r"^(<all_urls>|(\*|https?|wss?|ftp|file)://\*/.*)$")
_PLAIN_WORD = re.compile(r"^[A-Za-z][A-Za-z-]{0,40}$")


def _version_key(version: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[._]", version))


def _safe_listdir(path: Path) -> list:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ── processes ──────────────────────────────────────────────────────────────────

def list_processes(platform: str) -> list:
    """Return [(pid, ppid, executable)] from the OS. ppid is None where unavailable."""
    if platform == "win32":
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             timeout=20, check=True).stdout
        return [(row[1], None, row[0]) for row in csv.reader(io.StringIO(out)) if len(row) > 1]
    # macOS: comm is the executable path. Linux: comm is the (short) process name.
    flag = "-axo" if platform == "darwin" else "-eo"
    out = subprocess.run(["ps", flag, "pid=,ppid=,comm="], capture_output=True, text=True,
                         timeout=20, check=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


class _BundleIds:
    """Bundle ID of the outermost .app an executable lives in (macOS), cached."""

    def __init__(self):
        self._cache = {}

    def __call__(self, executable: str):
        match = _BUNDLE_PATH.match(executable or "")
        if not match:
            return None
        bundle = match.group(1)
        if bundle not in self._cache:
            self._cache[bundle] = _read_plist(Path(bundle) / "Contents" / "Info.plist").get("CFBundleIdentifier")
        return self._cache[bundle]


def _read_plist(path: Path) -> dict:
    try:
        with open(path, "rb") as handle:
            data = plistlib.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}


def _basename(executable: str) -> str:
    return PureWindowsPath(executable).name if "\\" in executable else PurePosixPath(executable).name


# ── code signatures (macOS): who published a program ───────────────────────────

def signing_target(executable: str):
    """What to check the signature of: the outermost .app, or a standalone program.

    Apple's own system programs are skipped; they are not what discovery looks for.
    """
    match = _BUNDLE_PATH.match(executable or "")
    if match:
        return match.group(1)
    if executable and (executable.startswith("/") or Path(executable).is_absolute()) and not executable.startswith(SYSTEM_PREFIXES):
        return executable
    return None


def read_signer(target: str):
    """Who signed a program, checked by macOS `codesign`.

    Returns the publisher's Team ID (or "apple") when the signature is valid,
    "unsigned" when there is no publisher identity (unsigned or ad-hoc),
    "invalid" when a signature exists but does not verify, and None if unknown.
    `--ignore-resources` verifies the code and its signature without hashing
    every resource file, which keeps large apps fast.

    Scripts (files starting with "#!", like npm and pip command shims) cannot
    carry a code signature at all; they return "script" instead of "unsigned".
    """
    if _is_script(target):
        return "script"
    try:
        shown = subprocess.run(["codesign", "-dv", "--verbose=2", target], capture_output=True,
                               text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if shown.returncode != 0:
        return "unsigned" if "not signed" in shown.stderr else None
    team = re.search(r"^TeamIdentifier=(\S+)\s*$", shown.stderr, re.MULTILINE)
    if team and team.group(1) != "not set":
        signer = team.group(1)
    elif re.search(r"^Authority=(?:macOS )?Software Signing\s*$", shown.stderr, re.MULTILINE):
        signer = "apple"
    else:
        return "unsigned"
    try:
        verified = subprocess.run(["codesign", "--verify", "--no-strict", "--ignore-resources", target],
                                  capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return signer if verified.returncode == 0 else "invalid"


_SIGNING_AUTHORITY = re.compile(
    r"^Authority=(?:Developer ID Application|Apple Development|Apple Distribution): (.+?) \(([A-Z0-9]{10})\)\s*$",
    re.MULTILINE)


def read_publisher(target: str, signer_reader=None) -> dict:
    """{"signer": ..., "name": ...}: who signed a program, with the publisher's name.

    The name is taken from the verified signing certificate, e.g. "Anthropic PBC"
    from "Developer ID Application: Anthropic PBC (Q6L2SF6YDW)". It is None when
    the program has no valid publisher signature.
    """
    signer = (signer_reader or read_signer)(target)
    if signer in (None, "unsigned", "invalid", "script"):
        return {"signer": signer, "name": None}
    if signer == "apple":
        return {"signer": signer, "name": "Apple"}
    try:
        shown = subprocess.run(["codesign", "-dv", "--verbose=2", target], capture_output=True,
                               text=True, timeout=10, check=False).stderr
    except (OSError, subprocess.SubprocessError):
        shown = ""
    match = next((m for m in _SIGNING_AUTHORITY.finditer(shown) if m.group(2) == signer), None)
    return {"signer": signer, "name": match.group(1) if match else None}


def _is_script(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:  # includes directories such as .app bundles
        return False


def read_signers(targets, signer_reader=read_signer, workers: int = 8) -> dict:
    unique = sorted({target for target in targets if target})
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(unique, pool.map(signer_reader, unique)))


def process_evidence(processes: list, platform: str, signers=None) -> list:
    bundle_id = _BundleIds() if platform == "darwin" else (lambda _executable: None)
    signers = signers or {}
    by_pid = {pid: executable for pid, _ppid, executable in processes}
    counts = {}
    for _pid, ppid, executable in processes:
        name = _basename(executable)
        if not name:
            continue
        item = {"kind": "process", "value": name}
        app = bundle_id(executable)
        if app:
            item["app"] = app
        signer = signers.get(signing_target(executable))
        if signer:
            item["signer"] = signer
        parent_executable = by_pid.get(ppid) if ppid is not None else None
        if parent_executable:
            item["parent"] = bundle_id(parent_executable) or _basename(parent_executable)
        key = tuple(sorted(item.items()))
        counts[key] = counts.get(key, 0) + 1
    return [{**dict(key), "count": count} for key, count in counts.items()]


# ── installed apps and their permissions (macOS) ───────────────────────────────

def read_entitlements(app_path: Path):
    """Entitlements declared in the app's code signature, or None if unknown."""
    try:
        result = subprocess.run(["codesign", "-d", "--entitlements", "-", "--xml", str(app_path)],
                                capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = plistlib.loads(result.stdout) if result.stdout.strip() else {}
    except (plistlib.InvalidFileException, ValueError):
        return None
    return data if isinstance(data, dict) else None


def app_permissions(entitlements: dict) -> list:
    """Entitlement names an app declares, plus whether it runs in the App Sandbox."""
    sandboxed = entitlements.get("com.apple.security.app-sandbox") is True
    permissions = {"sandbox:on" if sandboxed else "sandbox:off"}
    for key, value in entitlements.items():
        if value is True and key != "com.apple.security.app-sandbox":
            permissions.add("entitlement:" + key.removeprefix("com.apple.security."))
    return sorted(permissions)


def read_tcc_grants(db_paths):
    """{bundle_id: {permission names}} the user allowed, or None if macOS refuses access.

    macOS only lets processes with Full Disk Access read these databases.
    """
    grants, readable = {}, False
    for path in db_paths:
        if not Path(path).is_file():
            continue
        try:
            with closing(sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)) as db:
                rows = db.execute(
                    "SELECT service, client FROM access WHERE auth_value = 2 AND client_type = 0").fetchall()
        except sqlite3.Error:
            continue
        readable = True
        for service, client in rows:
            if isinstance(service, str) and isinstance(client, str):
                name = TCC_SERVICES.get(service, service.removeprefix("kTCCService").lower())
                grants.setdefault(client, set()).add(name)
    return grants if readable else None


def app_paths(home: Path, applications_dirs=None) -> list:
    dirs = applications_dirs if applications_dirs is not None else [Path("/Applications"), home / "Applications"]
    return [Path(directory) / entry for directory in dirs for entry in _safe_listdir(directory)
            if entry.endswith(".app")]


def app_evidence(home: Path, applications_dirs=None, entitlements_reader=read_entitlements, grants=None,
                 signers=None) -> list:
    grants, signers = grants or {}, signers or {}
    found = {}
    for app_path in app_paths(home, applications_dirs):
        info = _read_plist(app_path / "Contents" / "Info.plist")
        bundle_id = info.get("CFBundleIdentifier")
        if not isinstance(bundle_id, str) or not bundle_id or bundle_id in found:
            continue
        item = {"kind": "app", "value": bundle_id}
        version = info.get("CFBundleShortVersionString")
        if isinstance(version, str):
            item["version"] = version
        signer = signers.get(str(app_path))
        if signer:
            item["signer"] = signer
        entitlements = entitlements_reader(app_path)
        permissions = app_permissions(entitlements) if entitlements is not None else []
        permissions += sorted(f"granted:{name}" for name in grants.get(bundle_id, ()))
        if permissions:
            item["permissions"] = permissions
        found[bundle_id] = item
    return list(found.values())


# ── editor extensions (VS Code family, all platforms) ──────────────────────────

def _removed_extension_folders(extensions_dir: Path) -> set:
    """Folders the editor marked as uninstalled; they can stay on disk until it restarts."""
    obsolete = _read_json(extensions_dir / ".obsolete")
    return {folder for folder, removed in obsolete.items() if removed is True}


def editor_extension_evidence(home: Path) -> list:
    evidence = []
    for editor, relative in EDITOR_DIRS.items():
        newest = {}
        removed = _removed_extension_folders(home / relative)
        for folder in _safe_listdir(home / relative):
            if folder in removed:
                continue
            match = _EXTENSION_FOLDER.match(folder)
            if not match:
                continue
            ext_id, version = match.group("id").lower(), match.group("version")
            if ext_id not in newest or _version_key(version) > _version_key(newest[ext_id]):
                newest[ext_id] = version
        evidence += [{"kind": "editor-extension", "value": ext_id, "editor": editor, "version": version}
                     for ext_id, version in sorted(newest.items())]
    return evidence


# ── browser extensions and native messaging hosts (Chromium browsers) ──────────

def extension_permissions(manifest: dict) -> list:
    """Permission names from a browser extension manifest; sites reduced to all/some."""
    permissions, hosts = set(), []
    for permission in manifest.get("permissions") or []:
        if not isinstance(permission, str):
            continue
        if "://" in permission or permission == "<all_urls>":
            hosts.append(permission)  # Manifest V2 lists sites among permissions
        elif _PLAIN_WORD.match(permission):
            permissions.add("extension:" + permission.lower())
    hosts += [h for h in manifest.get("host_permissions") or [] if isinstance(h, str)]
    if hosts:
        permissions.add("hosts:all-sites" if any(_ALL_SITES.match(h) for h in hosts) else "hosts:some-sites")
    scripts = [match for script in manifest.get("content_scripts") or [] if isinstance(script, dict)
               for match in script.get("matches") or [] if isinstance(match, str)]
    if scripts:
        all_sites = any(_ALL_SITES.match(m) for m in scripts)
        permissions.add("content-scripts:all-sites" if all_sites else "content-scripts:some-sites")
    return sorted(permissions)


def browser_evidence(home: Path, platform: str) -> list:
    evidence, hosts = [], set()
    for browser, relative in CHROMIUM_ROOTS.get(platform, {}).items():
        root = home / relative
        for profile in _safe_listdir(root):
            if profile != "Default" and not profile.startswith("Profile "):
                continue
            for ext_id in _safe_listdir(root / profile / "Extensions"):
                if not _CHROME_EXTENSION_ID.match(ext_id):
                    continue
                versions = _safe_listdir(root / profile / "Extensions" / ext_id)
                item = {"kind": "browser-extension", "value": ext_id, "browser": browser, "profile": profile}
                if versions:
                    newest = max(versions, key=_version_key)
                    item["version"] = newest.split("_")[0]
                    manifest = _read_json(root / profile / "Extensions" / ext_id / newest / "manifest.json")
                    if manifest:
                        item["permissions"] = extension_permissions(manifest)
                evidence.append(item)
        if platform != "win32":  # Windows registers native hosts in the registry
            hosts.update((name[:-5], browser) for name in _safe_listdir(root / "NativeMessagingHosts")
                         if name.endswith(".json"))
    evidence += [{"kind": "native-host", "value": name, "browser": browser} for name, browser in sorted(hosts)]
    return evidence


# ── command-line tools and global packages (looked up by requested name only) ──

def _root(*parts) -> str:
    """Absolute path from parts, e.g. _root("opt", "homebrew", "bin")."""
    return os.path.join(os.sep, *parts)


# Requested names must look like a command or package name: no paths, no "..".
_REQUESTED_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]{0,100}$")
_WINDOWS_COMMAND = re.compile(r"\.(exe|cmd|bat|ps1)$", re.IGNORECASE)


def requested_names(names) -> list:
    return sorted({n.lower() for n in names or [] if isinstance(n, str) and _REQUESTED_NAME.match(n.lower())
                   and ".." not in n})


def command_dirs(home: Path, platform: str, path_env=None) -> list:
    """Folders where command-line tools are installed: PATH plus common install folders.

    System folders are left out; they hold the OS's own commands.
    """
    path_env = os.environ.get("PATH", "") if path_env is None else path_env
    dirs = [d for d in path_env.split(os.pathsep) if d]
    if platform != "win32":
        dirs += [_root("opt", "homebrew", "bin"), _root("usr", "local", "bin"), str(home / ".local" / "bin"),
                 str(home / "bin"), str(home / ".npm-global" / "bin"), str(home / ".bun" / "bin"),
                 str(home / ".cargo" / "bin"), str(home / ".volta" / "bin")]
    else:
        dirs += [str(home / "AppData" / "Roaming" / "npm"), str(home / ".local" / "bin")]
    dirs += version_manager_command_dirs(home, platform)
    unique = []
    for directory in dirs:
        normalized = os.path.normpath(os.path.expanduser(directory))
        if normalized not in unique and not (normalized + os.sep).startswith(SYSTEM_PREFIXES):
            unique.append(normalized)
    return unique


def find_commands(home: Path, platform: str, names, path_env=None, dirs=None) -> list:
    """[(name, real_path)] for requested command names that exist and are executable."""
    wanted = set(requested_names(names))
    found, seen = [], set()
    if not wanted:
        return found
    for directory in (dirs if dirs is not None else command_dirs(home, platform, path_env)):
        for entry in _safe_listdir(Path(directory)):
            name = entry.lower()
            if platform == "win32":
                # Windows runs files by extension, not by an "executable" flag.
                if not _WINDOWS_COMMAND.search(name):
                    continue
                name = _WINDOWS_COMMAND.sub("", name)
            if name not in wanted:
                continue
            full = os.path.join(directory, entry)
            if not os.path.isfile(full) or (platform != "win32" and not os.access(full, os.X_OK)):
                continue
            real = os.path.realpath(full)
            if (name, real) not in seen:  # the same program linked from two folders counts once
                seen.add((name, real))
                found.append((name, real))
    return found


def command_evidence(commands: list, signers=None) -> list:
    """One item per command name and signer; the program's location is never returned."""
    signers = signers or {}
    counts = {}
    for name, real in commands:
        item = {"kind": "cli", "value": name}
        signer = signers.get(signing_target(real))
        if signer:
            item["signer"] = signer
        key = tuple(sorted(item.items()))
        counts[key] = counts.get(key, 0) + 1
    return [{**dict(key), "count": count} for key, count in counts.items()]


# ── which requested tool a node/python process is running ──────────────────────

_INTERPRETER = re.compile(r"^(node|nodejs|bun|deno|pythonw?(\d+(\.\d+)*)?)$")
_NPM_PACKAGE_PATH = re.compile(r"node_modules/((?:@[^/\s\"']+/)?[^/\s\"']+)")
_PYTHON_TOOL_PATH = re.compile(r"(?:pipx/venvs|uv/tools)/([^/\s\"']+)")
_SCRIPT_SUFFIX = re.compile(r"\.(c?js|mjs|ts|py|exe|cmd)$", re.IGNORECASE)


def _interpreter_name(executable: str):
    name = _basename(executable or "").lower()
    name = name[:-4] if name.endswith(".exe") else name
    return name if _INTERPRETER.match(name) else None


def parse_windows_command_lines(text: str) -> dict:
    try:
        data = json.loads(text) if text and text.strip() else []
    except ValueError:
        return {}
    data = data if isinstance(data, list) else [data]
    return {str(row.get("ProcessId")): row["CommandLine"] for row in data
            if isinstance(row, dict) and isinstance(row.get("CommandLine"), str)}


def read_command_lines(pids, platform: str) -> dict:
    """{pid: command line} for the given processes only (the interpreters)."""
    pids = [str(pid) for pid in pids if str(pid).isdigit()]
    if not pids:
        return {}
    if platform == "win32":
        query = " OR ".join(f"ProcessId={pid}" for pid in pids)
        script = (f"Get-CimInstance Win32_Process -Filter '{query}' | "
                  "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=20, check=False).stdout
        return parse_windows_command_lines(out)
    out = subprocess.run(["ps", "-o", "pid=,args=", "-p", ",".join(pids)], capture_output=True, text=True,
                         timeout=20, check=False).stdout
    lines = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            lines[parts[0]] = parts[1]
    return lines


def match_interpreted(command_line: str, packages: set, commands: set):
    """The requested package or command an interpreter process runs, or None.

    Looks at the interpreter and the first few arguments (resolving links, so a
    command shim leads to its package folder). Nothing but the matched name
    leaves this function: other arguments, paths and secrets are never returned.
    """
    tokens = [token.strip("\"'") for token in command_line.split()[:6]]
    candidates = []
    for token in tokens:
        if not token or token.startswith("-"):
            continue
        candidates.append(token)
        if os.path.isabs(token) and os.path.exists(token):
            candidates.append(os.path.realpath(token))
    candidates.append(command_line[:600])  # quoted Windows paths may contain spaces
    for text in candidates:
        normalized = text.replace("\\", "/")
        for pattern in (_NPM_PACKAGE_PATH, _PYTHON_TOOL_PATH):
            for match in pattern.finditer(normalized):
                if match.group(1).lower() in packages:
                    return match.group(1).lower()
    for token in candidates[:-1]:
        name = _SCRIPT_SUFFIX.sub("", _basename(token).lower())
        if name in commands:
            return name
    return None


def interpreted_evidence(rows: list, platform: str, cli_names, package_names,
                         command_line_reader=read_command_lines) -> list:
    """Running node/python/bun/deno processes attributed to a requested tool."""
    packages, commands = set(requested_names(package_names)), set(requested_names(cli_names))
    interpreters = {pid: executable for pid, _ppid, executable in rows if _interpreter_name(executable)}
    if not (packages or commands) or not interpreters:
        return []
    try:
        lines = command_line_reader(list(interpreters), platform)
    except (OSError, subprocess.SubprocessError):
        return []
    bundle_id = _BundleIds() if platform == "darwin" else (lambda _executable: None)
    by_pid = {pid: (ppid, executable) for pid, ppid, executable in rows}
    counts = {}
    for pid, executable in interpreters.items():
        line = lines.get(str(pid))
        name = match_interpreted(line, packages, commands) if isinstance(line, str) else None
        if not name:
            continue
        item = {"kind": "interpreted-process", "value": name, "interpreter": _interpreter_name(executable)}
        parent = by_pid.get(by_pid[pid][0])
        if parent:
            item["parent"] = bundle_id(parent[1]) or _basename(parent[1])
        key = tuple(sorted(item.items()))
        counts[key] = counts.get(key, 0) + 1
    return [{**dict(key), "count": count} for key, count in counts.items()]


def node_installs(home: Path, platform: str, environ=None) -> list:
    """Install folders of every Node version from nvm, fnm, asdf, mise and nvm-windows.

    Each folder contains the Node version's global packages and commands.
    Custom locations from FNM_DIR, ASDF_DATA_DIR and MISE_DATA_DIR are honored.
    """
    environ = os.environ if environ is None else environ
    fnm_default = (home / "Library" / "Application Support" / "fnm" if platform == "darwin"
                   else home / "AppData" / "Roaming" / "fnm" if platform == "win32"
                   else home / ".local" / "share" / "fnm")
    patterns = [
        (home / ".nvm" / "versions" / "node", "*"),
        (Path(environ.get("FNM_DIR") or fnm_default) / "node-versions", "*/installation"),
        (home / ".fnm" / "node-versions", "*/installation"),
        (Path(environ.get("ASDF_DATA_DIR") or home / ".asdf") / "installs" / "nodejs", "*"),
        (Path(environ.get("MISE_DATA_DIR") or home / ".local" / "share" / "mise") / "installs" / "node", "*"),
    ]
    if platform == "win32":
        patterns.append((home / "AppData" / "Roaming" / "nvm", "v*"))
    installs = []
    for base, pattern in patterns:
        installs += [path for path in sorted(base.glob(pattern)) if path.is_dir() and not path.is_symlink()]
    return installs


def _node_modules(install: Path, platform: str) -> Path:
    # Windows Node keeps global packages next to node.exe; elsewhere under lib/.
    return install / "node_modules" if platform == "win32" else install / "lib" / "node_modules"


def version_manager_command_dirs(home: Path, platform: str, environ=None) -> list:
    """Command folders of Node version managers: shims plus each version's bin folder."""
    environ = os.environ if environ is None else environ
    dirs = [Path(environ.get("ASDF_DATA_DIR") or home / ".asdf") / "shims",
            Path(environ.get("MISE_DATA_DIR") or home / ".local" / "share" / "mise") / "shims"]
    dirs += [install if platform == "win32" else install / "bin" for install in node_installs(home, platform, environ)]
    return [str(directory) for directory in dirs]


def package_roots(home: Path, platform: str, environ=None) -> dict:
    roots = {"npm": [], "pipx": [home / ".local" / "pipx" / "venvs", home / ".local" / "share" / "pipx" / "venvs"],
             "uv": [home / ".local" / "share" / "uv" / "tools"]}
    if platform == "win32":
        roots["npm"].append(home / "AppData" / "Roaming" / "npm" / "node_modules")
        roots["uv"].append(home / "AppData" / "Roaming" / "uv" / "tools")
    else:
        roots["npm"] += [Path(_root("opt", "homebrew", "lib", "node_modules")),
                         Path(_root("usr", "local", "lib", "node_modules")),
                         home / ".npm-global" / "lib" / "node_modules"]
    roots["npm"] += [_node_modules(install, platform) for install in node_installs(home, platform, environ)]
    return roots


def package_evidence(home: Path, platform: str, names, roots_by_manager=None) -> list:
    """Globally installed npm, pipx and uv tool packages, looked up by requested name only."""
    evidence = []
    roots_by_manager = roots_by_manager if roots_by_manager is not None else package_roots(home, platform)
    for name in requested_names(names):
        for manager, roots in roots_by_manager.items():
            if manager != "npm" and name.startswith("@"):
                continue
            for root in roots:
                if manager == "npm":
                    manifest = Path(root) / name / "package.json"
                    if not manifest.is_file():
                        continue
                    item = {"kind": "package", "value": name, "manager": manager}
                    version = _read_json(manifest).get("version")
                    if isinstance(version, str) and re.match(r"^[\w.+-]{1,40}$", version):
                        item["version"] = version
                elif (Path(root) / name).is_dir():
                    item = {"kind": "package", "value": name, "manager": manager}
                else:
                    continue
                if item not in evidence:
                    evidence.append(item)
    return evidence


# ── agent configuration: MCP servers and Claude Code permission rules ──────────

def _mcp_items(servers, client: str, scope: str) -> list:
    if not isinstance(servers, dict):
        return []
    items = []
    for name, config in sorted(servers.items()):
        if not isinstance(name, str) or not name.strip():
            continue
        config = config if isinstance(config, dict) else {}
        declared = config.get("type")
        if isinstance(declared, str) and declared in MCP_TRANSPORTS:
            transport = declared
        else:
            transport = "stdio" if "command" in config else "http" if "url" in config else "other"
        items.append({"kind": "mcp-server", "value": name.strip(), "client": client,
                      "scope": scope, "transport": transport})
    return items


def mcp_evidence(home: Path, platform: str) -> list:
    """MCP server names per agent. Commands, arguments, env, URLs and project paths are never returned."""
    evidence = []
    desktop_config = CLAUDE_DESKTOP_CONFIG.get(platform)
    if desktop_config:
        evidence += _mcp_items(_read_json(home / desktop_config).get("mcpServers"), "claude-desktop", "user")
    claude_code = _read_json(home / ".claude.json")
    evidence += _mcp_items(claude_code.get("mcpServers"), "claude-code", "user")
    projects = claude_code.get("projects")
    for project in (projects.values() if isinstance(projects, dict) else []):
        if isinstance(project, dict):
            evidence += _mcp_items(project.get("mcpServers"), "claude-code", "project")
    evidence += _mcp_items(_read_json(home / ".cursor" / "mcp.json").get("mcpServers"), "cursor", "user")
    return evidence


def agent_rules_evidence(home: Path, platform: str, managed_settings=None) -> list:
    """Claude Code permission mode and rule counts. The rules themselves are never returned."""
    managed = managed_settings if managed_settings is not None else CLAUDE_CODE_MANAGED_SETTINGS.get(platform)
    evidence = []
    for scope, path in (("user", home / ".claude" / "settings.json"), ("managed", managed)):
        if path is None or not Path(path).is_file():
            continue
        rules = _read_json(Path(path)).get("permissions")
        rules = rules if isinstance(rules, dict) else {}
        mode = rules.get("defaultMode")
        evidence.append({
            "kind": "agent-rules", "value": "claude-code", "scope": scope,
            "mode": mode if isinstance(mode, str) and _PLAIN_WORD.match(mode) else "default",
            **{key: str(len(rules[key])) if isinstance(rules.get(key), list) else "0"
               for key in ("allow", "ask", "deny")},
        })
    return evidence


# ── entry point ────────────────────────────────────────────────────────────────

def collect(home=None, platform=None, processes=None, applications_dirs=None,
            entitlements_reader=read_entitlements, tcc_paths=None, managed_settings=None,
            signer_reader=read_signer, cli_names=(), package_names=(), path_env=None,
            command_dirs_override=None, package_roots_override=None,
            command_line_reader=read_command_lines) -> dict:
    """Collect device evidence. Arguments exist so tests can use a fake home, process list and readers.

    cli_names and package_names are the command and package names the catalog
    asks about; only those are looked up, so other installed tools stay private.
    """
    platform = platform or sys.platform
    home = Path(home) if home is not None else Path.home()
    evidence, collected, not_collected, errors = [], [], {}, []

    try:
        rows = processes if processes is not None else list_processes(platform)
    except (OSError, subprocess.SubprocessError) as error:
        rows = None
        errors.append(f"process list unavailable ({type(error).__name__})")

    commands = find_commands(home, platform, cli_names, path_env, command_dirs_override)

    # Who signed each running program, installed app and requested command, once per program.
    signers = {}
    if platform == "darwin":
        targets = [signing_target(executable) for _pid, _ppid, executable in rows or []]
        targets += [str(path) for path in app_paths(home, applications_dirs)]
        targets += [signing_target(real) for _name, real in commands]
        signers = read_signers(targets, signer_reader)
        collected.append("signer")
    else:
        not_collected["signer"] = "code signatures are only checked on macOS so far"

    if rows is not None:
        evidence += process_evidence(rows, platform, signers)
        collected.append("process")

    if rows is not None and (requested_names(cli_names) or requested_names(package_names)):
        evidence += interpreted_evidence(rows, platform, cli_names, package_names, command_line_reader)
        collected.append("interpreted-process")
    else:
        not_collected["interpreted-process"] = "no command or package names were requested"

    if requested_names(cli_names):
        evidence += command_evidence(commands, signers)
        collected.append("cli")
    else:
        not_collected["cli"] = "no command names were requested"
    if requested_names(package_names):
        evidence += package_evidence(home, platform, package_names, package_roots_override)
        collected.append("package")
    else:
        not_collected["package"] = "no package names were requested"

    if platform == "darwin":
        if tcc_paths is None:
            tcc_paths = [home / "Library/Application Support/com.apple.TCC/TCC.db",
                         Path("/Library/Application Support/com.apple.TCC/TCC.db")]
        grants = read_tcc_grants(tcc_paths)
        evidence += app_evidence(home, applications_dirs, entitlements_reader, grants, signers)
        collected += ["app", "app-permissions"]
        if grants is None:
            not_collected["granted-permissions"] = "macOS shows privacy grants only to apps with Full Disk Access"
        else:
            collected.append("granted-permissions")
    else:
        not_collected["app"] = "installed apps are only read on macOS so far"

    evidence += editor_extension_evidence(home)
    collected.append("editor-extension")

    if platform in CHROMIUM_ROOTS:
        evidence += browser_evidence(home, platform)
        collected.append("browser-extension")
        if platform == "win32":
            not_collected["native-host"] = "Windows registers native hosts in the registry"
        else:
            collected.append("native-host")
    else:
        not_collected["browser-extension"] = f"unsupported platform {platform}"

    evidence += mcp_evidence(home, platform)
    evidence += agent_rules_evidence(home, platform, managed_settings)
    collected += ["mcp-server", "agent-rules"]

    return {"platform": platform, "collected": collected, "not_collected": not_collected,
            "errors": errors, "evidence": evidence}


if __name__ == "__main__":
    import json

    print(json.dumps({"evidence_count": len(collect()["evidence"])}))
