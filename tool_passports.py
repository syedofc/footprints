"""Forkit Core agent passports for AI tools found by the device scan.

One passport per installation of a tool, for example "Claude Code, command-line
tool" or "Claude in Chrome, Chrome extension":

    name           <finding>/<surface>, e.g. claude-code/cli, claude-desktop/app
    version        the version the scan detects (app or extension metadata);
                   for command-line tools without version metadata, entered by the user
    creator        the publisher from the verified code signature, e.g.
                   "Anthropic PBC"; only for unsigned tools does the user vouch
    artifact_hash  fingerprint of the installed program (see FINGERPRINTS below)
    model_id       provider-reference model passport (Anthropic Claude, GitHub, ...)
    parent_agent_id  previous passport of the same installation (lineage)

The page only sends identifiers (finding, kind, bundle ID / command / extension
ID). The local service finds the program itself, so file paths never travel.

Fingerprints:
    app                SHA-256 of the main executable and of the code signature's
                       sealed resource list (_CodeSignature/CodeResources), which
                       covers every file of the app; Info.plist if unsigned
    cli                SHA-256 of the program file (for npm/pip scripts: the script)
    editor-extension   SHA-256 of the extension's package.json (weaker: no signature)
    browser-extension  SHA-256 of manifest.json and, if present, the Web Store's
                       signed file list (_metadata/verified_contents.json)
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from forkit.schemas import AgentPassport

from collector import (_CHROME_EXTENSION_ID, _EXTENSION_FOLDER, CHROMIUM_ROOTS, EDITOR_DIRS, _read_plist,
                       _removed_extension_folders, _safe_listdir, _version_key, app_paths, find_commands,
                       read_publisher, requested_names, signing_target)
from repo_passports import (PassportRequestError, _registry, existing_version, passport_display_name,
                            provider_model, same_version, scoped_name, scoped_versions, version_key)

SOURCE = "footprints-device-tool"
KINDS = ("app", "cli", "editor-extension", "browser-extension")
UNVERIFIED = (None, "unsigned", "invalid", "script")

_FINDING = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
_BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,200}$")
_EXTENSION_ID = re.compile(r"^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9.-]*$")
_PROFILE = re.compile(r"^(Default|Profile \d{1,3})$")
_LEADING_VERSION = re.compile(r"^(\d{1,6})\.(\d{1,6})(?:\.(\d{1,6}))?")

TOOL_PROVIDERS = {
    "claude-desktop": "anthropic", "claude-code": "anthropic", "claude-in-chrome": "anthropic",
    "github-copilot": "github", "copilot-cli": "github", "gemini-cli": "google",
    "cursor": "cursor", "cursor-agent-cli": "cursor", "codex-cli": "openai",
    "perplexity": "perplexity", "grok-bot": "xai",
}
TASK_TYPES = {"Coding agent": "code-assistant", "AI editor": "code-assistant", "Editor extension": "code-assistant",
              "AI app": "personal-assistant", "Browser extension": "personal-assistant"}

def _sha256_file(path: Path):
    """Read the file again for each check, even when size and mtime match."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def fingerprint(parts) -> str | None:
    """SHA-256 over (role, file hash) pairs. Roles, not paths, so the location does not matter."""
    combined = hashlib.sha256()
    for role, path in parts:
        file_hash = _sha256_file(path)
        if file_hash is None:
            return None
        combined.update(f"{role}\0{file_hash}\n".encode("utf-8"))
    return combined.hexdigest()


def detected_version(text):
    match = _LEADING_VERSION.match(text or "")
    return ".".join(group for group in match.groups() if group is not None) if match else None


def surface(installation: dict) -> str:
    kind = installation.get("kind", "unknown")
    if kind == "editor-extension":
        return f"{installation.get('editor', 'unknown')}-extension"
    if kind == "browser-extension":
        return f"{installation.get('browser', 'unknown')}-extension"
    return kind


def validate_installation(request: dict, platform: str) -> dict:
    kind, value = request.get("kind"), request.get("value")
    if kind not in KINDS or not isinstance(value, str):
        raise PassportRequestError("Choose an installation of a scanned tool.")
    installation = {"kind": kind, "value": value}
    if kind == "app":
        valid = bool(_BUNDLE_ID.match(value))
    elif kind == "cli":
        valid = requested_names([value]) == [value]
    elif kind == "editor-extension":
        installation["editor"] = request.get("editor")
        valid = bool(_EXTENSION_ID.match(value)) and installation["editor"] in EDITOR_DIRS
    else:
        installation["browser"], installation["profile"] = request.get("browser"), request.get("profile")
        valid = (bool(_CHROME_EXTENSION_ID.match(value)) and installation["browser"] in CHROMIUM_ROOTS.get(platform, {})
                 and bool(_PROFILE.match(str(installation["profile"]))))
    if not valid:
        raise PassportRequestError("That installation is not valid.")
    return installation


def locate(installation: dict, home: Path, platform: str, applications_dirs=None, command_dirs=None):
    """Find an installation on this device: {"parts", "version", "signing_target"} or None."""
    kind, value = installation["kind"], installation["value"]
    if kind == "app":
        for app in app_paths(home, applications_dirs):
            info = _read_plist(app / "Contents" / "Info.plist")
            if info.get("CFBundleIdentifier") != value:
                continue
            executable = app / "Contents" / "MacOS" / str(info.get("CFBundleExecutable") or "")
            seal = app / "Contents" / "_CodeSignature" / "CodeResources"
            second = ("code-resources", seal) if seal.is_file() else ("info-plist", app / "Contents" / "Info.plist")
            return {"parts": [("executable", executable), second],
                    "version": detected_version(str(info.get("CFBundleShortVersionString") or "")),
                    "signing_target": str(app)}
        return None
    if kind == "cli":
        found = find_commands(home, platform, [value], dirs=command_dirs)
        if not found:
            return None
        real = found[0][1]
        return {"parts": [("program", Path(real))], "version": None, "signing_target": signing_target(real) or real}
    if kind == "editor-extension":
        folder = home / EDITOR_DIRS[installation["editor"]]
        removed = _removed_extension_folders(folder)
        newest = None
        for name in _safe_listdir(folder):
            match = _EXTENSION_FOLDER.match(name)
            if match and name not in removed and match.group("id").lower() == value.lower():
                if newest is None or _version_key(match.group("version")) > _version_key(newest[1]):
                    newest = (name, match.group("version"))
        if newest is None:
            return None
        return {"parts": [("package-json", folder / newest[0] / "package.json")],
                "version": detected_version(newest[1]), "signing_target": None}
    root = home / CHROMIUM_ROOTS[platform][installation["browser"]] / installation["profile"] / "Extensions" / value
    versions = _safe_listdir(root)
    if not versions:
        return None
    newest = max(versions, key=_version_key)
    parts = [("manifest", root / newest / "manifest.json")]
    verified = root / newest / "_metadata" / "verified_contents.json"
    if verified.is_file():
        parts.append(("web-store-verified-contents", verified))
    return {"parts": parts, "version": detected_version(newest.split("_")[0]), "signing_target": None}


def create_tool_passport(registry_root: Path, request: dict, home=None, platform=None,
                         applications_dirs=None, command_dirs=None, signer_reader=None) -> dict:
    home = Path(home) if home is not None else Path.home()
    platform = platform or sys.platform
    finding, label = request.get("finding"), str(request.get("label") or "").strip()[:80]
    if not isinstance(finding, str) or not _FINDING.match(finding) or not label:
        raise PassportRequestError("Choose a scanned tool.")
    display_name = passport_display_name(request)
    installation = validate_installation(request, platform)
    found = locate(installation, home, platform, applications_dirs, command_dirs)
    if found is None:
        raise PassportRequestError("This installation is no longer on this device. Scan again.", 404)

    publisher = ({"signer": None, "name": None} if platform != "darwin" or not found["signing_target"]
                 else read_publisher(found["signing_target"], signer_reader))
    if publisher["signer"] == "invalid":
        raise PassportRequestError("The program's signature does not verify. No passport was created.", 409)
    verified = publisher["signer"] not in UNVERIFIED
    if verified:
        creator = {"name": publisher["name"] or f"Team {publisher['signer']}", "organization": None}
    else:
        name = str(request.get("creator") or "").strip()
        if not 0 < len(name) <= 100 or "\n" in name:
            raise PassportRequestError("This tool has no verified publisher. Enter who vouches for it.",
                                       publisher_vouch_required=True)
        creator = {"name": name, "organization": str(request.get("organization") or "").strip()[:100] or None}

    version = found["version"] or detected_version(str(request.get("version") or "").strip())
    if not version or version_key(version) is None or (not found["version"] and str(request.get("version")).strip() != version):
        raise PassportRequestError("Enter the installed version, like 1.0 or 1.0.0 (numbers only).")

    artifact_hash = fingerprint(found["parts"])
    if artifact_hash is None:
        raise PassportRequestError("The installation files could not all be read. No passport was created.", 409)
    registry = _registry(registry_root)
    legacy_name = f"{finding}/{surface(installation)}"
    name = scoped_name(legacy_name, (SOURCE, finding, *installation.values()))
    previous, parent = scoped_versions(registry, name, legacy_name, lambda context: (
        context.get("source") == SOURCE and context.get("finding") == finding
        and all(context.get(key) == value for key, value in installation.items())
    ), version)
    model = provider_model(registry, TOOL_PROVIDERS.get(finding, "unspecified"))
    passport = AgentPassport(
        name=name, version=version, creator=creator, model_id=model.id,
        task_type=TASK_TYPES.get(request.get("tool_kind"), "other"), architecture="Tool-Use", role="assistant",
        artifact_hash=artifact_hash, parent_agent_id=parent,
        deployment_env="local", status="active",
        description=f"{label}, {surface(installation).replace('-', ' ')}",
        metadata={"context": {
            "source": SOURCE, "finding": finding, "label": label, "tool_kind": request.get("tool_kind"),
            "display_name": display_name,
            **installation,
            "publisher_team": publisher["signer"] if verified else None,
            "creator_source": "code-signature" if verified else "user",
            "version_source": "detected" if found["version"] else "user",
            "fingerprinted": [role for role, _path in found["parts"]],
        }},
    )
    taken = same_version(previous, passport)
    existing = existing_version(taken, passport)
    if existing is not None:
        return {"status": "already-registered", "passport": existing, "check": registry.verify_passport(existing["id"])}
    if taken:
        raise PassportRequestError(
            f"Version {version} already has a passport for a different program. The program changed without a new "
            "version number: it may have been rebuilt or replaced.", 409)
    if registry.get_agent(passport.id) is not None:
        raise PassportRequestError("This ID already exists with conflicting local metadata.", 409)
    registry.register_agent(passport)
    return {"status": "registered", "passport": passport.to_dict(), "check": registry.verify_passport(passport.id)}


def tool_state(data: dict, context: dict, home: Path, platform: str, applications_dirs=None, command_dirs=None,
               signer_reader=None) -> dict:
    """Compare a tool passport with what is installed now."""
    try:
        installation = validate_installation(context, platform)
    except PassportRequestError:
        return {"install": "unknown", "publisher": "not-checked"}
    found = locate(installation, home, platform, applications_dirs, command_dirs)
    if found is None:
        return {"install": "missing", "publisher": "not-checked"}
    state = {"current_version": found["version"]}
    current_hash = fingerprint(found["parts"])
    if current_hash is None:
        state["install"] = "unreadable"
    elif current_hash == data.get("artifact_hash"):
        state["install"] = "unchanged"
    elif found["version"] and found["version"] != data.get("version"):
        state["install"] = "updated"
    else:
        state["install"] = "changed"
    team = context.get("publisher_team")
    if team and platform == "darwin" and found["signing_target"]:
        current = read_publisher(found["signing_target"], signer_reader)["signer"]
        if current is None:
            state["publisher"] = "not-checked"
        elif current == "invalid":
            state["publisher"] = "invalid"
        else:
            state["publisher"] = "verified" if current == team else "changed"
    else:
        state["publisher"] = "user" if context.get("creator_source") == "user" else "not-checked"
    return state


def installation_summary(context: dict) -> str:
    kind = context.get("kind")
    if kind == "cli":
        return f"command {context.get('value')}"
    if kind == "app":
        return context.get("value", "")
    if kind == "editor-extension":
        return f"{context.get('value')} in {context.get('editor')}"
    return f"{context.get('value', '')[:12]}… in {context.get('browser')} ({context.get('profile')})"

