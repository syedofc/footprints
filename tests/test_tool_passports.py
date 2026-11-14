"""Tool passports for scanned AI tools, against Forkit Core's real registry with fake installations."""

import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repo_passports import PassportRequestError, list_passports
from tool_passports import create_tool_passport

TEAM = "ABCDE12345"
AUTHORITY = f"Authority=Developer ID Application: Example Corp ({TEAM})\nTeamIdentifier={TEAM}\n"


def make_app(applications: Path, bundle_id: str, version: str, code: bytes = b"program") -> Path:
    contents = applications / "Tool.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "_CodeSignature").mkdir(exist_ok=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": bundle_id, "CFBundleExecutable": "Tool", "CFBundleShortVersionString": version}))
    (contents / "MacOS" / "Tool").write_bytes(code)
    (contents / "_CodeSignature" / "CodeResources").write_bytes(b"sealed list of every file " + code)
    return applications / "Tool.app"


def codesign_names(args, **_kwargs):
    return subprocess.CompletedProcess(args, 0, "", AUTHORITY)


class ToolPassportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home, self.registry, self.apps, self.bin = root / "home", root / "registry", root / "Applications", root / "bin"
        for folder in (self.home, self.apps, self.bin):
            folder.mkdir()
        self.signer = TEAM
        self.options = {"applications_dirs": [self.apps], "command_dirs": [str(self.bin)],
                        "signer_reader": lambda _target: self.signer}
        patcher = mock.patch("collector.subprocess.run", side_effect=codesign_names)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def create(self, **request):
        request = {"finding": "claude-desktop", "label": "Claude desktop app", "tool_kind": "AI app",
                   "kind": "app", "value": "com.example.tool", **request}
        return create_tool_passport(self.registry, request, home=self.home, platform="darwin", **self.options)

    def state(self, passport_id):
        listed = list_passports(self.registry, self.home, "darwin", tool_state_options=self.options)["passports"]
        entry = next(p for p in listed if p["id"] == passport_id)
        return entry["install"], entry["publisher"]

    def test_app_passport_uses_the_signature_publisher_and_detected_version(self):
        make_app(self.apps, "com.example.tool", "4.16336.0.1")
        result = self.create(creator="Ignored Person", version="9.9.9")
        passport = result["passport"]
        self.assertEqual(result["status"], "registered")
        self.assertRegex(passport["name"], r"^claude-desktop/app/[a-f0-9]{64}$")
        self.assertEqual(passport["version"], "4.16336.0")
        self.assertEqual(passport["creator"], {"name": "Example Corp", "organization": None, "email": None, "url": None})
        context = passport["metadata"]["context"]
        self.assertEqual((context["creator_source"], context["publisher_team"], context["version_source"]),
                         ("code-signature", TEAM, "detected"))
        self.assertEqual(context["fingerprinted"], ["executable", "code-resources"])
        self.assertNotIn(self._tmp.name, json.dumps(passport))
        self.assertEqual(self.create()["status"], "already-registered")

    def test_checks_follow_the_installed_program(self):
        app = make_app(self.apps, "com.example.tool", "1.0.0")
        passport_id = self.create()["passport"]["id"]
        self.assertEqual(self.state(passport_id), ("unchanged", "verified"))

        self.signer = "OTHERTEAM1"
        self.assertEqual(self.state(passport_id), ("unchanged", "changed"))
        self.signer = TEAM

        make_app(self.apps, "com.example.tool", "1.0.0", code=b"patched program")   # same version, new code
        self.assertEqual(self.state(passport_id), ("changed", "verified"))
        with self.assertRaises(PassportRequestError) as same_version:
            self.create()
        self.assertEqual(same_version.exception.status, 409)

        make_app(self.apps, "com.example.tool", "1.1.0", code=b"new release")
        self.assertEqual(self.state(passport_id)[0], "updated")
        newer = self.create()["passport"]
        self.assertEqual((newer["version"], newer["parent_agent_id"]), ("1.1.0", passport_id))

        shutil.rmtree(app)
        self.assertEqual(self.state(passport_id)[0], "missing")

    def test_refuses_a_program_whose_signature_does_not_verify(self):
        make_app(self.apps, "com.example.tool", "1.0.0")
        self.signer = "invalid"
        with self.assertRaises(PassportRequestError) as invalid:
            self.create()
        self.assertEqual(invalid.exception.status, 409)

    def test_unsigned_command_line_tool_needs_a_person_and_a_version(self):
        program = self.bin / "aider"
        program.write_text("#!" + os.path.join(os.sep, "usr", "bin", "env") + " python\n")
        program.chmod(0o755)
        self.signer = "script"
        request = {"finding": "aider", "label": "Aider", "tool_kind": "Coding agent", "kind": "cli", "value": "aider"}
        for missing in ({"version": "1.0.0"}, {"creator": "Jane"}, {"creator": "Jane", "version": "v1.0.0"}):
            with self.assertRaises(PassportRequestError, msg=missing):
                self.create(**request, **missing)
        passport = self.create(**request, creator="Jane", version="0.86.1", display_name="My terminal agent")["passport"]
        self.assertRegex(passport["name"], r"^aider/cli/[a-f0-9]{64}$")
        self.assertEqual(passport["creator"]["name"], "Jane")
        self.assertEqual(passport["metadata"]["context"]["creator_source"], "user")
        self.assertEqual(passport["metadata"]["context"]["display_name"], "My terminal agent")
        self.assertEqual(self.state(passport["id"]), ("unchanged", "user"))
        program.write_text("#!/bin/sh\necho replaced\n")
        self.assertEqual(self.state(passport["id"])[0], "changed")

    def test_editor_and_browser_extensions(self):
        extensions = self.home / ".vscode" / "extensions"
        for folder, version in (("anthropic.claude-code-2.1.300-darwin-arm64", "2.1.300"),
                                ("anthropic.claude-code-2.1.322-darwin-arm64", "2.1.322")):
            (extensions / folder).mkdir(parents=True)
            (extensions / folder / "package.json").write_text(json.dumps({"version": version}))
        editor = self.create(finding="claude-code", label="Claude Code", kind="editor-extension",
                             value="anthropic.claude-code", editor="vscode", creator="Jane")["passport"]
        self.assertRegex(editor["name"], r"^claude-code/vscode-extension/[a-f0-9]{64}$")
        self.assertEqual(editor["version"], "2.1.322")

        chrome = self.home / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Extensions" / ("a" * 32)
        (chrome / "1.0.106_0" / "_metadata").mkdir(parents=True)
        (chrome / "1.0.106_0" / "manifest.json").write_text("{}")
        (chrome / "1.0.106_0" / "_metadata" / "verified_contents.json").write_text("[]")
        browser = self.create(finding="claude-in-chrome", label="Claude in Chrome", kind="browser-extension",
                              value="a" * 32, browser="chrome", profile="Default", creator="Jane")["passport"]
        self.assertRegex(browser["name"], r"^claude-in-chrome/chrome-extension/[a-f0-9]{64}$")
        self.assertEqual(browser["version"], "1.0.106")
        self.assertEqual(browser["metadata"]["context"]["fingerprinted"], ["manifest", "web-store-verified-contents"])

    def test_browser_profiles_do_not_share_ids_or_lineage(self):
        request = dict(finding="claude-in-chrome", label="Claude in Chrome", kind="browser-extension",
                       value="a" * 32, browser="chrome", creator="Example")
        root = self.home / "Library" / "Application Support" / "Google" / "Chrome"
        for profile in ("Default", "Profile 1"):
            folder = root / profile / "Extensions" / request["value"] / "1.0.0_0"
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text("{}")
        first = self.create(**request, profile="Default")["passport"]
        second = self.create(**request, profile="Profile 1")["passport"]
        self.assertEqual(first["artifact_hash"], second["artifact_hash"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertIsNone(second["parent_agent_id"])
        newer = root / "Profile 1" / "Extensions" / request["value"] / "2.0.0_0"
        newer.mkdir()
        (newer / "manifest.json").write_text("{}")
        self.assertEqual(self.create(**request, profile="Profile 1")["passport"]["parent_agent_id"], second["id"])

    def test_fingerprint_and_publisher_checks_report_unavailable_evidence(self):
        app = make_app(self.apps, "com.example.tool", "1.0.0")
        passport_id = self.create()["passport"]["id"]
        program = app / "Contents" / "MacOS" / "Tool"
        stat = program.stat()
        program.write_bytes(b"replace")
        os.utime(program, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.signer = None
        self.assertEqual(self.state(passport_id), ("changed", "not-checked"))
        self.signer = "invalid"
        self.assertEqual(self.state(passport_id), ("changed", "invalid"))
        self.signer = TEAM
        program.unlink()
        self.assertEqual(self.state(passport_id), ("unreadable", "verified"))
        with self.assertRaises(PassportRequestError):
            self.create()

    def test_rejects_identifiers_that_are_not_plain_names(self):
        for request in ({"value": "../../etc"}, {"kind": "cli", "value": "a b"}, {"kind": "process"},
                        {"kind": "browser-extension", "value": "a" * 32, "browser": "chrome", "profile": "../x"},
                        {"finding": "../x"}):
            with self.assertRaises(PassportRequestError, msg=request):
                self.create(creator="Jane", **request)
        with self.assertRaises(PassportRequestError) as missing:
            self.create(creator="Jane")
        self.assertEqual(missing.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
