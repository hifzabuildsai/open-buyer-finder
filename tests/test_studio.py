"""Real localhost HTTP tests; research/provider calls are the isolated boundary."""
import copy
import http.client
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from buyerfind.studio import create_server


def bundle():
    return {"schema_version": 2, "id": "fixture", "mode": "synthetic_demo",
            "status": "partial", "stage": "done", "product_url": "https://example.test",
            "product": {"name": "Fictional product <script>alert(1)</script>"},
            "companies": [], "segments": [], "competitors": [], "sequences": [],
            "warnings": ["Synthetic HTTP test"], "summary": {"target_met": False}}


def fake_save(path, data):
    Path(path).mkdir(parents=True, exist_ok=True)
    (Path(path) / "bundle.json").write_text(json.dumps(data), encoding="utf-8")


class StudioHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {"model": {"provider": "ollama", "endpoint": "http://127.0.0.1:11434",
                                 "api_key": "never-send-this-secret"},
                       "search": {"provider": "brave", "api_key_env": "TEST_STUDIO_SEARCH_KEY"},
                       "enrichment": {"provider": "public"},
                       "limits": {"companies": 100, "pages": 30}}
        self.server = create_server(self.config, self.tmp.name, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def call(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = None if data is None else json.dumps(data)
        supplied = {"Host": self.server.host}
        if data is not None:
            supplied.update({"Content-Type": "application/json", "Origin": self.server.origin,
                             "X-Studio-Token": self.server.token})
        supplied.update(headers or {})
        conn.request(method, path, body=body, headers=supplied)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_static_shell_and_security_headers(self):
        status, headers, body = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Open Buyer Finder", body)
        self.assertNotIn(b"__STUDIO_TOKEN__", body)
        self.assertIn(self.server.token.encode(), body)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.call("GET", "/app.js")[0], 200)
        self.assertEqual(self.call("GET", "/style.css")[0], 200)

    def test_foreign_host_and_absolute_request_target_rejected(self):
        for host in ["attacker.example", "127.0.0.1.attacker.example", "localhost", "127.0.0.1:1"]:
            self.assertEqual(self.call("GET", "/api/state", headers={"Host": host})[0], 403)
        self.assertEqual(self.call("GET", "http://attacker.example/api/state")[0], 400)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.putrequest("GET", "/", skip_host=True)
        connection.putheader("Host", self.server.host)
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()

    def test_mutations_require_exact_origin_and_token(self):
        self.assertEqual(self.call("POST", "/api/demo", {}, {"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/demo", {}, {"Origin": "null"})[0], 403)
        self.assertEqual(self.call("POST", "/api/demo", {}, {"X-Studio-Token": "wrong"})[0], 403)
        self.assertEqual(self.call("POST", "/api/demo", {}, {"X-Studio-Token": "é"})[0], 403)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_only_bounded_json_url_requests_are_accepted(self):
        for payload in [{}, {"url": "javascript:alert(1)"}, {"url": "https://key:secret@example.test"},
                        {"url": "https://example.test", "api_key": "browser-secret"},
                        {"url": "https://example.test", "output": "../elsewhere"}, []]:
            self.assertEqual(self.call("POST", "/api/research", payload)[0], 400)
        self.assertEqual(self.call("POST", "/api/demo", {"api_key": "bad"})[0], 400)
        self.assertEqual(self.call("POST", "/api/demo", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("POST", "/api/demo", {"extra": "a" * 9000})[0], 413)
        self.assertEqual(self.call("POST", "/api/send", {})[0], 404)

    def test_state_redacts_config_and_identifies_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status, headers, body = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn(b"never-send-this-secret", body)
        self.assertNotIn(b"TEST_STUDIO_SEARCH_KEY", body)
        self.assertNotIn(b"11434", body)
        state = json.loads(body)
        search = next(row for row in state["providers"] if row["category"] == "search")
        self.assertFalse(search["configured"])
        self.assertEqual(search["note"], "API key missing")
        self.assertEqual(state["limits"]["companies"], 100)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_safe_run_loading_and_no_arbitrary_file_access(self):
        data = bundle()
        fake_save(Path(self.tmp.name) / "saved-run", data)
        status, headers, body = self.call("GET", "/api/runs/saved-run")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body)["product"], data["product"])
        self.assertNotIn(b"<script>", self.call("GET", "/")[2])
        state = json.loads(self.call("GET", "/api/state")[2])
        self.assertEqual(state["runs"][0]["name"], "saved-run")
        for path in ["/api/runs/../", "/api/runs/%2e%2e", "/api/runs/saved-run/bundle.json", "/../../etc/passwd", "/studio.py"]:
            self.assertEqual(self.call("GET", path)[0], 404)

    def test_symlinked_runs_and_bundle_files_are_not_exposed(self):
        outside = Path(self.tmp.name).parent / (Path(self.tmp.name).name + "-outside")
        try:
            fake_save(outside, bundle())
            (Path(self.tmp.name) / "linked-run").symlink_to(outside, target_is_directory=True)
            inside = Path(self.tmp.name) / "linked-file"
            inside.mkdir()
            (inside / "bundle.json").symlink_to(outside / "bundle.json")
            self.assertEqual(self.call("GET", "/api/runs/linked-run")[0], 400)
            self.assertEqual(self.call("GET", "/api/runs/linked-file")[0], 400)
            self.assertEqual(json.loads(self.call("GET", "/api/state")[2])["runs"], [])
        finally:
            (outside / "bundle.json").unlink(missing_ok=True)
            outside.rmdir()

    def test_demo_job_writes_explicitly_synthetic_bundle(self):
        module = types.ModuleType("buyerfind.market")
        module.demo_bundle = bundle
        module.save_bundle = fake_save
        module.run_pipeline = lambda *args, **kwargs: self.fail("Demo must not run live research")
        with mock.patch.dict(sys.modules, {"buyerfind.market": module}):
            status, _, body = self.call("POST", "/api/demo", {})
            self.assertEqual(status, 202)
            job = json.loads(body)
            self.server.worker.join(timeout=2)
        result = json.loads(self.call("GET", "/api/runs/" + job["run_name"])[2])
        self.assertEqual(result["mode"], "synthetic_demo")
        self.assertFalse(result["summary"]["target_met"])

    def test_only_one_job_runs_and_progress_is_observable(self):
        entered, release = threading.Event(), threading.Event()
        module = types.ModuleType("buyerfind.market")
        module.demo_bundle, module.save_bundle = bundle, fake_save

        def pipeline(url, config, output, progress):
            result = bundle()
            result.update(mode="live", status="running", stage="competitors", product_url=url)
            progress(copy.deepcopy(result))
            entered.set()
            release.wait(timeout=3)
            result.update(status="partial", stage="done")
            fake_save(output, result)
            return result

        module.run_pipeline = pipeline
        try:
            with mock.patch.dict(sys.modules, {"buyerfind.market": module}):
                status, _, body = self.call("POST", "/api/research", {"url": "https://product.example.test"})
                self.assertEqual(status, 202)
                self.assertTrue(entered.wait(timeout=1))
                self.assertEqual(self.call("POST", "/api/demo", {})[0], 409)
                state = json.loads(self.call("GET", "/api/state")[2])
                self.assertTrue(state["job"]["active"])
                self.assertEqual(state["job"]["stage"], "competitors")
                run = json.loads(body)["run_name"]
                progress = json.loads(self.call("GET", "/api/runs/" + run)[2])
                self.assertEqual(progress["status"], "running")
                release.set()
                self.server.worker.join(timeout=2)
            self.assertFalse(json.loads(self.call("GET", "/api/state")[2])["job"]["active"])
        finally:
            release.set()

    def test_provider_exception_does_not_leak_credentials(self):
        module = types.ModuleType("buyerfind.market")
        module.demo_bundle, module.save_bundle = bundle, fake_save

        def broken_pipeline(url, config, output, progress):
            checkpoint = bundle()
            checkpoint.update(mode="live", status="running", stage="companies")
            progress(checkpoint)
            raise ValueError("https://service.test?api_key=secret-leak")

        module.run_pipeline = broken_pipeline
        with mock.patch.dict(sys.modules, {"buyerfind.market": module}):
            self.assertEqual(self.call("POST", "/api/research", {"url": "https://example.test"})[0], 202)
            self.server.worker.join(timeout=2)
        body = self.call("GET", "/api/state")[2]
        self.assertNotIn(b"secret-leak", body)
        job = json.loads(body)["job"]
        self.assertEqual(job["status"], "failed")
        progress = json.loads(self.call("GET", "/api/runs/" + job["run_name"])[2])
        self.assertEqual(progress["status"], "failed")
        self.assertNotIn("secret-leak", json.dumps(progress))

    def test_csv_download_is_not_an_external_sync(self):
        fake_save(Path(self.tmp.name) / "saved-run", bundle())
        module = types.ModuleType("buyerfind.exports")
        module.export_csv = lambda data, kind: "name,mode\r\nFictional,synthetic_demo\r\n"
        with mock.patch.dict(sys.modules, {"buyerfind.exports": module}):
            status, headers, body = self.call("GET", "/api/runs/saved-run/export?kind=companies")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="saved-run-companies.csv"')
        self.assertIn(b"synthetic_demo", body)
        self.assertEqual(self.call("GET", "/api/runs/saved-run/export?kind=../../secret")[0], 400)
        self.assertEqual(self.call("GET", "/api/runs/saved-run/export?kind=companies&kind=contacts")[0], 400)


if __name__ == "__main__":
    unittest.main()
