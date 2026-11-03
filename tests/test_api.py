import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend import main


class DebugPilotApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        main.DB_PATH = Path(cls.temp_dir.name) / "test.sqlite3"
        main.initialize_database()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp_dir.cleanup()

    def post(self, path, body):
        request = Request(f"{self.base_url}{path}", data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            return response.status, json.load(response)

    def get(self, path):
        with urlopen(f"{self.base_url}{path}") as response:
            return json.load(response)

    def test_ingest_groups_duplicate_errors_and_redacts_secrets(self):
        event = {"service": "payment-service", "endpoint": "/api/payments", "status_code": 500,
                 "error": "Database connection timeout token=do-not-store"}
        status, first = self.post("/api/events", event)
        self.assertEqual(status, 201)
        self.assertEqual(first["event"]["type"], "error_detected")
        self.assertTrue(first["event"]["occurred_at"].endswith("Z"))
        _, second = self.post("/api/events", event)
        self.assertTrue(second["grouped"])
        self.assertEqual(second["incident"]["occurrence_count"], 2)
        self.assertNotIn("do-not-store", json.dumps(self.get("/api/incidents")))

    def test_investigation_actions_are_recorded(self):
        _, created = self.post("/api/events", {"service": "user-service", "endpoint": "/api/users",
                                               "status_code": 503, "error": "database unavailable"})
        incident_id = created["incident"]["id"]
        self.assertEqual(len(self.get(f"/api/incidents/{incident_id}")["events"]), 1)
        _, investigated = self.post(f"/api/incidents/{incident_id}/investigate", {})
        self.assertEqual([event["type"] for event in investigated["events"][-3:]],
                         ["investigation_started", "evidence_collected", "hypothesis_identified"])
        self.assertIn("not confirmed", investigated["events"][-1]["payload"]["summary"])

    def test_invalid_event_returns_validation_error(self):
        request = Request(f"{self.base_url}/api/events", data=b'{"service":"bad name","endpoint":"/x","status_code":500}',
                          headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as raised:
            urlopen(request)
        self.assertEqual(raised.exception.code, 400)
        raised.exception.close()

    def test_dashboard_assets_and_incident_report_workflow(self):
        with urlopen(f"{self.base_url}/") as response:
            self.assertIn(b"DebugPilot", response.read())
        for path in ("/assets/styles.css", "/assets/app.js"):
            with urlopen(f"{self.base_url}{path}") as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(int(response.headers["Content-Length"]), 100)

        _, created = self.post("/api/events", {"service": "product-service", "endpoint": "/api/products",
                                               "status_code": 500, "error": "Unexpected response"})
        incident_id = created["incident"]["id"]
        self.post(f"/api/incidents/{incident_id}/investigate", {})
        _, test = self.post(f"/api/incidents/{incident_id}/tests", {"action": "generate"})
        _, result = self.post(f"/api/tests/{test['id']}/execute", {})
        self.assertEqual(result["result"]["status"], "passed")
        self.assertIn("not executed", result["result"]["scope"])
        _, report = self.post(f"/api/incidents/{incident_id}/reports", {})
        stored_report = self.get(f"/api/reports/{report['id']}")
        timeline_types = [event["type"] for event in stored_report["timeline"]]
        self.assertIn("test_executed", timeline_types)
        self.assertIn("report_generated", timeline_types)


if __name__ == "__main__":
    unittest.main()