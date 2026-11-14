import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from serve import create_footprints_app


class FootprintsIntegrationTest(unittest.TestCase):
    def test_local_ui_and_core_share_an_origin_and_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_root = Path(directory) / "Footprints registry café"
            with TestClient(
                create_footprints_app(registry_root), base_url="http://127.0.0.1"
            ) as client:
                page = client.get("/footprints/")
                self.assertEqual(page.status_code, 200)
                self.assertIn('id="passport-form"', page.text)
                self.assertEqual(page.headers["x-frame-options"], "DENY")
                for script in ("discovery.js", "passport.js"):
                    self.assertEqual(client.get(f"/footprints/{script}").status_code, 200)
                self.assertEqual(client.get("/footprints/serve.py").status_code, 404)
                self.assertEqual(client.get("http://external.example/footprints/").status_code, 400)

                artifact_hash = hashlib.sha256(b"synthetic model artifact").hexdigest()
                payload = {
                    "name": "integration-model",
                    "version": "1.0.0",
                    "creator": {"name": "Test fixture"},
                    "task_type": "embedding",
                    "architecture": "encoder-only",
                    "artifact_hash": artifact_hash,
                    "artifact_files": ["model.safetensors"],
                }
                created = client.post(
                    "/models", json=payload, headers={"Origin": "http://127.0.0.1"}
                )
                self.assertEqual(created.status_code, 201, created.text)
                passport_id = created.json()["id"]
                stored_path = registry_root / "models" / f"{passport_id}.json"
                passport = json.loads(stored_path.read_text(encoding="utf-8"))
                self.assertEqual(passport["artifact_hash"], artifact_hash)

                verify_path = f"/verify/{passport_id}"
                verified = client.post(verify_path)
                self.assertEqual(verified.status_code, 200, verified.text)
                self.assertTrue(verified.json()["valid"])
                self.assertEqual(verified.json()["stored_id"], passport_id)

                listed = client.post("/passports/list", json={})
                self.assertEqual(listed.status_code, 200, listed.text)
                model = next(item for item in listed.json()["passports"] if item["id"] == passport_id)
                self.assertEqual(model["artifact_hash"], artifact_hash)
                self.assertEqual(model["artifact_files"], ["model.safetensors"])

                passport["name"] = "changed after registration"
                stored_path.write_text(json.dumps(passport), encoding="utf-8")
                rejected = client.post(verify_path)
                self.assertEqual(rejected.status_code, 409, rejected.text)
                self.assertEqual(rejected.json()["error"]["code"], "id_mismatch")

    def test_device_scan_is_post_only_and_same_origin(self):
        fake_scan = {"platform": "test", "collected": ["process"], "not_collected": {}, "errors": [],
                     "evidence": [{"kind": "process", "value": "claude", "parent": "zsh", "count": 1}]}
        requests = []

        def fake_collect(**names):
            requests.append(names)
            return fake_scan

        with tempfile.TemporaryDirectory() as directory:
            app = create_footprints_app(Path(directory), collect_evidence=fake_collect)
            with TestClient(app, base_url="http://127.0.0.1") as client:
                scanned = client.post("/discovery/scan", json={
                    "cli": ["claude", "Copilot", "../../bin/sh", 42],
                    "packages": ["@anthropic-ai/claude-code", "a/b/c"],
                }, headers={"Origin": "http://127.0.0.1"})
                self.assertEqual(scanned.status_code, 200, scanned.text)
                self.assertEqual(scanned.json(), fake_scan)
                # Only plain command and package names reach the collector.
                self.assertEqual(requests[-1], {"cli_names": ["claude", "copilot"],
                                                "package_names": ["@anthropic-ai/claude-code"]})
                self.assertEqual(client.post("/discovery/scan", json={}).status_code, 200)
                self.assertEqual(requests[-1], {"cli_names": [], "package_names": []})

                self.assertEqual(client.get("/discovery/scan").status_code, 404)
                # A plain form post (what another website could send) is refused.
                self.assertEqual(client.post("/discovery/scan", data={"cli": "claude"}).status_code, 415)
                self.assertEqual(client.post("/discovery/scan", json=["claude"]).status_code, 422)
                external = client.post("/discovery/scan", json={}, headers={"Origin": "https://external.example"})
                self.assertEqual(external.status_code, 403)
                self.assertEqual(client.post("http://external.example/discovery/scan", json={}).status_code, 400)

    def test_repository_scan_is_post_only_json_and_local(self):
        fake = {"scanned_at": "T", "repos": [], "changes": None, "previous_scan_at": None, "scans_in_history": 1}
        with tempfile.TemporaryDirectory() as directory:
            app = create_footprints_app(Path(directory), scan_repos=lambda: fake)
            with TestClient(app, base_url="http://127.0.0.1") as client:
                self.assertEqual(client.get("/footprints/repositories.js").status_code, 200)
                scanned = client.post("/repos/scan", json={}, headers={"Origin": "http://127.0.0.1"})
                self.assertEqual(scanned.headers["cache-control"], "no-store")
                self.assertEqual(scanned.status_code, 200, scanned.text)
                self.assertEqual(scanned.json(), fake)
                self.assertEqual(client.get("/repos/scan").status_code, 404)
                self.assertEqual(client.post("/repos/scan", data={"a": "b"}).status_code, 415)
                external = client.post("/repos/scan", json={}, headers={"Origin": "https://external.example"})
                self.assertEqual(external.status_code, 403)

    def test_repository_scan_saves_history_in_the_registry_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_footprints_app(Path(directory)), base_url="http://127.0.0.1") as client:
                with mock.patch("repos.find_repositories", return_value=[]):
                    first = client.post("/repos/scan", json={}).json()
                    second = client.post("/repos/scan", json={}).json()
            self.assertIsNone(first["changes"])
            self.assertEqual(second["changes"], {"new": [], "not_found": [], "changed": []})
            self.assertTrue((Path(directory) / "repos-history.json").is_file())

    def test_passport_endpoints_are_json_local_and_report_errors(self):
        from repo_passports import PassportRequestError

        with tempfile.TemporaryDirectory() as directory, \
                TestClient(create_footprints_app(Path(directory)), base_url="http://127.0.0.1") as client:
            self.assertEqual(client.get("/footprints/passports.js").status_code, 200)
            for page_file in ("", "ui.js", "discovery.js", "repositories.js", "passports.js", "passport.js"):
                self.assertEqual(client.get(f"/footprints/{page_file}").headers["cache-control"], "no-cache", page_file)
            with mock.patch("serve.list_passports", return_value={"passports": []}) as listed:
                self.assertEqual(client.post("/passports/list", json={}).json(), {"passports": []})
                listed.assert_called_once_with(Path(directory))
            refused = PassportRequestError("Version 1.0.0 already has a passport.", 409, suggested_version="1.1.0")
            with mock.patch("serve.create_repo_agent_passport", side_effect=refused):
                conflict = client.post("/passports/repo-agent", json={"repo": "~/a", "tool": "claude-code"},
                                       headers={"Origin": "http://127.0.0.1"})
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json(), {"detail": "Version 1.0.0 already has a passport.", "suggested_version": "1.1.0"})
            # The real handler without a repository scan refuses politely.
            no_scan = client.post("/passports/repo-agent", json={"repo": "~/a", "tool": "claude-code",
                                                                  "version": "1.0.0", "creator": "Jane"})
            self.assertEqual((no_scan.status_code, no_scan.json()["detail"]), (409, "Scan repositories first."))
            self.assertEqual(client.post("/passports/repo-agent", data={"a": "b"}).status_code, 415)
            invalid = PassportRequestError("The program's signature does not verify. No passport was created.", 409)
            with mock.patch("serve.create_tool_passport", side_effect=invalid):
                refused_tool = client.post("/passports/tool", json={"kind": "app", "value": "com.example.tool"})
            self.assertEqual((refused_tool.status_code, refused_tool.json()["detail"]), (409, str(invalid)))
            self.assertEqual(client.post("/passports/tool", json={"kind": "process"}).status_code, 422)
            self.assertEqual(client.post("/passports/tool", data={"a": "b"}).status_code, 415)
            self.assertEqual(client.get("/passports/list").status_code, 404)
            external = client.post("/passports/list", json={}, headers={"Origin": "https://external.example"})
            self.assertEqual(external.status_code, 403)

    def test_local_app_rejects_unsafe_core_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_root = Path(directory)
            with TestClient(
                create_footprints_app(registry_root), base_url="http://127.0.0.1"
            ) as client:
                self.assertEqual(client.post("/agents", json={}).status_code, 404)
                self.assertEqual(client.post("/sync/passports", json={}).status_code, 404)
                self.assertEqual(client.get("/export").status_code, 404)
                self.assertEqual(client.get("/passports/not-a-passport-id").status_code, 404)
                self.assertEqual(client.post("/models", json={"id": "../other"}).status_code, 422)
                self.assertEqual(client.post("/models", content="{}").status_code, 415)
                external = client.post(
                    "/models", json={}, headers={"Origin": "https://external.example"}
                )
                self.assertEqual(external.status_code, 403)
                self.assertEqual(list((registry_root / "models").iterdir()), [])
                self.assertFalse((registry_root / "other.json").exists())
