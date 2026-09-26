"""HTTP boundary and artifact tests for the loopback dashboard."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from forge.server import Dashboard, make_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dashboard = Dashboard(root=Path(self.temp.name) / "runs")
        self.server = make_server(0, self.dashboard)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = "127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Host": self.host, **(headers or {})}
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        status = response.status
        content_type = response.getheader("Content-Type")
        conn.close()
        return status, content_type, data

    def json_request(self, method, path, body=None, headers=None):
        status, kind, data = self.request(method, path, body, headers)
        return status, kind, json.loads(data)

    def test_host_and_origin_restrictions(self):
        self.assertEqual(self.json_request("GET", "/api/state", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.json_request("POST", "/api/demo", {}, headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.json_request("POST", "/api/demo", {}, headers={"Origin": "https://attacker.example"})[0], 403)
        status, _, created = self.json_request("POST", "/api/demo", {}, headers={"Origin": "http://localhost:%d" % self.server.server_port})
        self.assertEqual(status, 202)
        deadline = time.monotonic() + 15
        while self.dashboard.detail(created["id"])["status"] in ("running", "queued"):
            self.assertLess(time.monotonic(), deadline, "demo did not finish")
            time.sleep(0.05)

    def test_post_requires_bounded_json_object(self):
        self.assertEqual(self.json_request("POST", "/api/demo", b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.json_request("POST", "/api/demo", b"{", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.json_request("POST", "/api/demo", b"[]", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.json_request("POST", "/api/demo", b"x" * 100001, headers={"Content-Type": "application/json"})[0], 400)

    def test_state_and_demo_report_patch_endpoints(self):
        status, kind, initial = self.json_request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIn("application/json", kind)
        self.assertEqual(initial["runs"], [])
        status, _, created = self.json_request("POST", "/api/demo", {})
        self.assertEqual(status, 202)
        run_id = created["id"]
        deadline = time.monotonic() + 15
        while True:
            status, _, detail = self.json_request("GET", "/api/runs/" + run_id)
            self.assertEqual(status, 200)
            if detail["status"] not in ("running", "queued"):
                break
            self.assertLess(time.monotonic(), deadline, "demo did not finish")
            time.sleep(0.05)
        self.assertEqual(detail["status"], "verified", detail["summary"])
        self.assertTrue(detail["demo"])
        self.assertTrue(detail["verification"]["passed"])
        self.assertTrue(detail["events"])
        status, _, state = self.json_request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["runs"][0]["id"], run_id)
        self.assertEqual(state["runs"][0]["patch"], "")
        status, kind, patch_body = self.request("GET", "/api/runs/%s/patch" % run_id)
        self.assertEqual(status, 200)
        self.assertIn("text/plain", kind)
        self.assertEqual(patch_body.decode(), detail["patch"])
        status, kind, report_body = self.request("GET", "/api/runs/%s/report" % run_id)
        self.assertEqual(status, 200)
        self.assertIn("text/markdown", kind)
        self.assertIn("Scripted fixture, no model inference", report_body.decode())

    def test_unconfigured_live_run_and_unknown_artifact(self):
        with patch.dict("os.environ", {"AI_MODEL": "", "AI_API_KEY": ""}):
            status, _, body = self.json_request("POST", "/api/run", {"repo": "/tmp/repo", "issue": "Fix bug"})
        self.assertEqual(status, 400)
        self.assertIn("AI_API_KEY", body["error"])
        self.assertEqual(self.json_request("GET", "/api/runs/unknown")[0], 404)
        self.assertEqual(self.json_request("GET", "/api/runs/../patch")[0], 404)


if __name__ == "__main__":
    unittest.main()
