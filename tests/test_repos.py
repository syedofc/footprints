"""Repository tracker tests on fake repositories: no real git data is read."""

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import repos
from repos import (ai_configuration, compare, describe_repository, find_repositories, known_folders,
                   normalize_remote, scan_repositories)

ZERO = "0" * 40


def make_repo(folder: Path, remote=None, reflog=()):
    """A minimal repository: .git/config with an optional remote, and reflog lines."""
    git = folder / ".git"
    (git / "logs").mkdir(parents=True)
    config = "[core]\n\tbare = false\n"
    if remote:
        config += f'[remote "origin"]\n\turl = {remote}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
        config += "\tfetch = +refs/tags/*:refs/tags/*\n"  # duplicate keys must not break parsing
    (git / "config").write_text(config)
    if reflog:
        (git / "logs" / "HEAD").write_text("".join(
            f"{ZERO} {'a' * 40} Jane Doe <jane@example.com> {when} +0100\t{message}\n" for when, message in reflog))
    return folder


class RepoTrackerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_remote_urls_lose_credentials_and_become_host_owner_repo(self):
        cases = {
            "https://user:SECRET@git.example.com/team/app.git?token=SECRET#x": "git.example.com/team/app",
            "git@git.example.com:team/app.git": "git.example.com/team/app",
            "ssh://git@git.example.com:2222/team/app.git": "git.example.com/team/app",
            "file:///srv/mirror/app.git": "local",
            os.path.join(os.sep, "srv", "mirror", "app.git"): "local",
            "../other-repo": "local",
            "": None,
        }
        for url, expected in cases.items():
            self.assertEqual(normalize_remote(url), expected, url)

    def test_describes_clone_time_last_activity_and_hides_personal_data(self):
        repo = make_repo(self.home / "Projects" / "app", "https://jane:SECRET-TOKEN@git.example.com/team/app.git", [
            (1730000000, "clone: from https://jane:SECRET-TOKEN@git.example.com/team/app.git"),
            (1731000000, "commit: fix the payment bug for ACME"),
        ])
        (repo / "CLAUDE.md").write_text("SECRET-INSTRUCTIONS: never share")
        info = describe_repository(repo, self.home)
        self.assertEqual(info["path"], "~/Projects/app")
        self.assertEqual(info["origin"], "git.example.com/team/app")
        self.assertEqual(info["how"], "cloned")
        self.assertEqual(info["cloned_at"], "2024-10-27T03:33:20+00:00")
        self.assertEqual((info["last_active"], info["last_action"]), ("2024-11-07T17:20:00+00:00", "commit"))
        self.assertEqual(info["ai"], {"files": ["CLAUDE.md"]})
        text = json.dumps(info)
        for private in ("SECRET-TOKEN", "jane", "Jane", "example.com>", "ACME", "payment", "SECRET-INSTRUCTIONS"):
            self.assertNotIn(private, text)

    def test_a_repository_created_locally_is_not_reported_as_cloned(self):
        repo = make_repo(self.home / "code" / "notes", reflog=[(1730000000, "commit (initial): first")])
        info = describe_repository(repo, self.home)
        self.assertEqual((info["how"], info["origin"]), ("created here", None))

    def test_skips_explicit_network_share_paths(self):
        remote = Path("//example.invalid/share/project")
        with unittest.mock.patch.object(Path, "is_dir", side_effect=AssertionError("network path probed")):
            self.assertEqual(find_repositories(self.home, roots=[remote], known=[remote]), [])

    def test_ai_configuration_names_counts_and_server_names_only(self):
        repo = make_repo(self.home / "app")
        for name in ("AGENTS.md", ".github/copilot-instructions.md", ".cursor/rules/style.mdc",
                     ".cursor/rules/tests.mdc", ".claude/agents/reviewer.md"):
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text("SECRET-TEXT")
        (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "github": {"command": "npx", "env": {"GITHUB_TOKEN": "SECRET-ENV"}}}}))
        (repo / ".claude" / "settings.json").write_text(json.dumps({"permissions": {
            "defaultMode": "acceptEdits", "allow": ["Bash(npm test)"], "deny": ["Read(./secrets/**)"]}}))
        config = ai_configuration(repo)
        self.assertEqual(config, {
            "files": [".github/copilot-instructions.md", "AGENTS.md"],
            "folders": {".claude/agents": 1, ".cursor/rules": 2},
            "mcp_servers": [{"config": ".mcp.json", "server": "github"}],
            "claude_rules": [{"config": ".claude/settings.json", "mode": "acceptEdits", "allow": 1, "ask": 0, "deny": 1}],
        })
        text = json.dumps(config)
        for private in ("SECRET-TEXT", "SECRET-ENV", "npm test", "secrets"):
            self.assertNotIn(private, text)

    def test_finds_repositories_in_common_folders_but_not_too_deep_or_inside_others(self):
        projects = self.home / "Projects"
        make_repo(projects / "app")
        make_repo(projects / "app" / "vendor-copy" / "lib")              # inside a repo: not separate
        make_repo(projects / "client" / "web")
        make_repo(projects / "node_modules" / "pkg")                     # skipped folder
        make_repo(projects / "a" / "b" / "c" / "d" / "too-deep")
        (self.home / "Code").symlink_to(projects)                        # same folder under two names
        found = find_repositories(self.home, "darwin", roots=[projects, self.home / "Code"], known=[])
        self.assertEqual(sorted(path.name for path in found), ["app", "web"])

    def test_finds_repositories_that_agents_and_editors_know(self):
        repo = make_repo(Path(self._tmp.name) / "elsewhere" / "service")
        (repo / "src").mkdir()
        (self.home / ".claude.json").write_text(json.dumps({"projects": {str(repo / "src"): {}}}))
        db = self.home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
        db.parent.mkdir(parents=True)
        editor_repo = make_repo(Path(self._tmp.name) / "other" / "tool")
        with closing(sqlite3.connect(db)) as connection:
            connection.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
            connection.execute("INSERT INTO ItemTable VALUES (?, ?)", ("history.recentlyOpenedPathsList", json.dumps(
                {"entries": [{"folderUri": editor_repo.as_uri()}, {"folderUri": "vscode-remote://ssh/x"}]})))
            connection.commit()
        self.assertEqual(len(known_folders(self.home, "darwin")), 2)
        found = find_repositories(self.home, "darwin", roots=[])
        self.assertEqual(sorted(path.name for path in found), ["service", "tool"])

    def test_worktrees_with_a_gitdir_file_are_repositories(self):
        main = make_repo(self.home / "Projects" / "main", "git@git.example.com:team/main.git")
        worktree = self.home / "Projects" / "feature"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {main / '.git'}\n")
        found = find_repositories(self.home, "darwin", roots=[self.home / "Projects"], known=[])
        self.assertEqual(sorted(path.name for path in found), ["feature", "main"])
        self.assertEqual(describe_repository(worktree, self.home)["origin"], "git.example.com/team/main")

    def test_history_shows_new_missing_and_changed_repositories(self):
        history = Path(self._tmp.name) / "registry" / "repos-history.json"
        projects = self.home / "Projects"
        make_repo(projects / "kept", reflog=[(1730000000, "commit (initial): x")])
        make_repo(projects / "removed")
        first = scan_repositories(history, self.home, "darwin", roots=[projects], known=[], now="T1")
        self.assertIsNone(first["changes"])

        (projects / "kept" / "AGENTS.md").write_text("x")
        for path in sorted((projects / "removed").rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        (projects / "removed").rmdir()
        make_repo(projects / "new-clone", reflog=[(1731000000, "clone: from x")])
        second = scan_repositories(history, self.home, "darwin", roots=[projects], known=[], now="T2")
        kept, removed, new = (f"~/Projects/{name}" for name in ("kept", "removed", "new-clone"))
        self.assertEqual(second["previous_scan_at"], "T1")
        self.assertEqual(second["changes"], {"new": [new], "not_found": [removed],
                                             "changed": [{"path": kept, "fields": ["ai", "agents"]}]})
        self.assertEqual(json.loads(history.read_text())["scans"][-1]["scanned_at"], "T2")

    def test_history_keeps_only_the_latest_scans(self):
        history = Path(self._tmp.name) / "history.json"
        original = repos.HISTORY_LIMIT
        repos.HISTORY_LIMIT = 3
        try:
            for index in range(5):
                result = scan_repositories(history, self.home, "darwin", roots=[], known=[], now=f"T{index}")
        finally:
            repos.HISTORY_LIMIT = original
        self.assertEqual(result["scans_in_history"], 3)
        self.assertEqual([scan["scanned_at"] for scan in json.loads(history.read_text())["scans"]], ["T2", "T3", "T4"])

    def test_compare_ignores_unchanged_repositories(self):
        repo = {"path": "~/a", "origin": "x/y", "last_active": "T", "ai": {}}
        self.assertEqual(compare([repo], [dict(repo)]), {"new": [], "not_found": [], "changed": []})


if __name__ == "__main__":
    unittest.main()
