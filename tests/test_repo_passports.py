"""Agent passports for repository agent setups, against Forkit Core's real registry in a temp folder."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from repo_passports import PassportRequestError, create_repo_agent_passport, list_passports
from repos import agent_setups, scan_repositories


def make_repo(folder: Path, files: dict):
    (folder / ".git" / "logs").mkdir(parents=True)
    (folder / ".git" / "config").write_text('[remote "origin"]\n\turl = git@git.example.com:team/app.git\n')
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
    return folder


class RepoPassportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home, self.registry, self.history = root / "home", root / "registry", root / "registry" / "repos-history.json"
        self.repo = make_repo(self.home / "Projects" / "app", {
            "CLAUDE.md": "SECRET-INSTRUCTIONS",
            ".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(npm test)"]}}),
            ".claude/settings.local.json": "{}",
            ".mcp.json": json.dumps({"mcpServers": {"github": {"command": "npx", "env": {"TOKEN": "SECRET-ENV"}}}}),
            "AGENTS.md": "x",
        })

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self):
        return scan_repositories(self.history, self.home, "darwin", roots=[self.home / "Projects"], known=[])

    def create(self, **request):
        request = {"repo": "~/Projects/app", "tool": "claude-code", "version": "1.0.0", "creator": "Jane", **request}
        return create_repo_agent_passport(self.registry, self.history, request, home=self.home)

    def test_agent_setups_fingerprint_contents_but_not_personal_files(self):
        setups = {setup["tool"]: setup for setup in agent_setups(self.repo)}
        self.assertEqual(sorted(setups), ["agents-md", "claude-code"])
        self.assertEqual(setups["claude-code"]["files"], [".claude/settings.json", ".mcp.json", "CLAUDE.md"])
        before = setups["claude-code"]["fingerprint"]

        (self.repo / ".claude" / "settings.local.json").write_text('{"changed": true}')  # personal: ignored
        self.assertEqual({s["tool"]: s for s in agent_setups(self.repo)}["claude-code"]["fingerprint"], before)
        (self.repo / "CLAUDE.md").write_text("edited")
        self.assertNotEqual({s["tool"]: s for s in agent_setups(self.repo)}["claude-code"]["fingerprint"], before)

        copy = self.home / "elsewhere" / "app"
        shutil.copytree(self.repo, copy)
        self.assertEqual(agent_setups(copy), agent_setups(self.repo))  # location does not matter

    def test_creates_a_passport_with_fingerprint_model_and_mcp_names_only(self):
        self.scan()
        result = self.create(organization="Example Team", display_name="My review agent")
        self.assertEqual(result["status"], "registered")
        self.assertTrue(result["check"]["valid"])
        passport = result["passport"]
        self.assertRegex(passport["name"], r"^app/claude-code/[a-f0-9]{64}$")
        self.assertEqual((passport["version"], passport["passport_type"]), ("1.0.0", "agent"))
        self.assertEqual(passport["creator"]["organization"], "Example Team")
        self.assertEqual([tool["name"] for tool in passport["tools"]], ["github"])
        self.assertEqual(passport["artifact_hash"], {s["tool"]: s for s in agent_setups(self.repo)}["claude-code"]["fingerprint"])
        self.assertIsNone(passport["parent_agent_id"])
        text = json.dumps(passport)
        for private in ("SECRET-INSTRUCTIONS", "SECRET-ENV", "npm test", str(self.home)):
            self.assertNotIn(private, text)
        saved = list_passports(self.registry, self.home)["passports"]
        self.assertEqual(next(p for p in saved if p["id"] == passport["id"])["display_name"], "My review agent")
        models = [p for p in saved if p["kind"] == "provider-model"]
        self.assertEqual([(m["name"], m["id"]) for m in models], [("anthropic-claude", passport["model_id"])])

    def test_never_overwrites_and_links_new_versions(self):
        self.scan()
        first = self.create()["passport"]
        self.assertEqual(self.create()["status"], "already-registered")

        (self.repo / "CLAUDE.md").write_text("edited")
        self.scan()
        with self.assertRaises(PassportRequestError) as same_version:
            self.create()
        self.assertEqual((same_version.exception.status, same_version.exception.extra), (409, {"suggested_version": "1.1.0"}))
        second = self.create(version="1.1.0")["passport"]
        self.assertEqual(second["parent_agent_id"], first["id"])
        self.assertNotEqual(second["id"], first["id"])

    def test_same_name_clones_have_independent_ids_and_lineage(self):
        clone = self.home / "Projects" / "other" / "app"
        shutil.copytree(self.repo, clone)
        self.scan()
        first = self.create()["passport"]
        second = self.create(repo="~/Projects/other/app")["passport"]
        self.assertEqual(first["artifact_hash"], second["artifact_hash"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertIsNone(second["parent_agent_id"])
        newer = self.create(repo="~/Projects/other/app", version="2.0")["passport"]
        self.assertEqual(newer["parent_agent_id"], second["id"])
        self.assertEqual(self.create(version="3.0")["passport"]["parent_agent_id"], first["id"])
        self.assertEqual(self.create(version="1.5")["passport"]["parent_agent_id"], first["id"])
        self.assertEqual(self.create(version="1.0")["passport"]["id"], first["id"])

    def test_unreadable_setup_cannot_have_a_matching_fingerprint(self):
        from unittest import mock

        self.scan()
        saved = self.create()["passport"]["id"]
        with mock.patch("repos.setup_fingerprint", return_value=None):
            listed = list_passports(self.registry, self.home)["passports"]
            self.assertEqual(next(row for row in listed if row["id"] == saved)["setup"], "unreadable")
            with self.assertRaises(PassportRequestError):
                self.create(version="2.0")

    def test_legacy_id_is_retained_for_the_same_repository(self):
        from forkit.domain.integrity import verify_passport_id
        from forkit.registry import LocalRegistry
        from forkit.schemas import AgentPassport

        self.scan()
        sample = self.create()["passport"]
        sample["name"] = "app/claude-code"
        sample["id"] = verify_passport_id(sample)["derived_id"]
        registry = LocalRegistry(self.registry)
        registry.delete(self.create()["passport"]["id"])
        registry.register_agent(AgentPassport.from_dict(sample))
        self.assertEqual(self.create()["passport"]["id"], sample["id"])
        self.assertEqual(self.create(version="2.0")["passport"]["parent_agent_id"], sample["id"])

    def test_rejects_bad_requests(self):
        with self.assertRaises(PassportRequestError) as no_scan:
            self.create()
        self.assertEqual(no_scan.exception.status, 409)
        self.scan()
        for request, status in [({"version": "v1.0.0"}, 422), ({"version": "latest.build"}, 422), ({"creator": " "}, 422),
                                ({"tool": "cursor"}, 404), ({"tool": "not-a-tool"}, 422), ({"repo": "~/Projects/other"}, 404)]:
            with self.assertRaises(PassportRequestError) as error:
                self.create(**request)
            self.assertEqual(error.exception.status, status, request)

    def test_checks_whether_the_setup_still_matches_and_the_record_is_intact(self):
        self.scan()
        passport_id = self.create()["passport"]["id"]

        def entry():
            return next(p for p in list_passports(self.registry, self.home)["passports"] if p["id"] == passport_id)

        def state():
            saved = entry()
            return saved["setup"], saved["record"]

        self.assertEqual(state(), ("unchanged", "intact"))
        self.assertEqual([scan["state"] for scan in entry()["scan_history"]], ["matches"])
        (self.repo / "CLAUDE.md").write_text("edited")
        self.assertEqual(state(), ("changed", "intact"))
        self.scan()
        self.assertEqual([scan["state"] for scan in entry()["scan_history"]], ["different", "matches"])
        (self.repo / ".mcp.json").unlink()
        self.assertEqual(entry()["files_removed"], [".mcp.json"])
        for name in ("CLAUDE.md", ".claude/settings.json"):
            (self.repo / name).unlink()
        self.assertEqual(state(), ("files-removed", "intact"))
        shutil.rmtree(self.repo)
        self.assertEqual(state(), ("repo-missing", "intact"))

        stored = self.registry / "agents" / f"{passport_id}.json"
        data = json.loads(stored.read_text())
        data["name"] = "someone-else/claude-code"
        stored.write_text(json.dumps(data))
        self.assertEqual(state()[1], "id_mismatch")


if __name__ == "__main__":
    unittest.main()
