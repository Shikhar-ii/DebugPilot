from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DB_PATH = Path(__file__).resolve().parent / "debugpilot.sqlite3"
SERVICE_NAMES = ("user-service", "payment-service", "product-service")
SECRET_PATTERN = re.compile(r"(?i)\b(password|token|secret|api[_-]?key)\b\s*[:=]\s*[^,\s]+")


class NotFound(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY, service TEXT NOT NULL, endpoint TEXT NOT NULL,
                severity TEXT NOT NULL, status TEXT NOT NULL, error_type TEXT NOT NULL,
                error_message TEXT NOT NULL, fingerprint TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS incidents_fingerprint_status ON incidents(fingerprint, status);
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, incident_id TEXT REFERENCES incidents(id),
                event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_incident_time ON events(incident_id, occurred_at);
            CREATE TABLE IF NOT EXISTS investigations (
                incident_id TEXT PRIMARY KEY REFERENCES incidents(id), confidence REAL NOT NULL,
                root_cause TEXT NOT NULL, evidence TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_cases (
                id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id),
                title TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT
            );
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id),
                created_at TEXT NOT NULL, body TEXT NOT NULL
            );
        """)


def add_event(db: sqlite3.Connection, event_type: str, payload: dict, incident_id: str | None = None) -> dict:
    event = {"id": f"EV-{uuid.uuid4().hex[:12].upper()}", "incident_id": incident_id,
             "type": event_type, "occurred_at": now_iso(), "payload": payload}
    db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
               (event["id"], incident_id, event_type, event["occurred_at"], json.dumps(payload)))
    return event


def get_incident(db: sqlite3.Connection, incident_id: str) -> dict:
    row = db.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if row is None:
        raise NotFound("Incident not found")
    incident = dict(row)
    investigation = db.execute("SELECT * FROM investigations WHERE incident_id = ?", (incident_id,)).fetchone()
    incident["investigation"] = None
    if investigation:
        incident["investigation"] = {**dict(investigation), "evidence": json.loads(investigation["evidence"])}
    incident["events"] = [
        {**dict(item), "payload": json.loads(item["payload"])}
        for item in db.execute(
            "SELECT id, event_type AS type, occurred_at, payload FROM events WHERE incident_id = ? ORDER BY occurred_at, rowid",
            (incident_id,),
        ).fetchall()
    ]
    return incident


def classify_error(message: str) -> str:
    value = message.lower()
    if "timeout" in value or "timed out" in value:
        return "timeout"
    if "database" in value or "connection pool" in value:
        return "database_error"
    return "http_5xx"


def ingest_event(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Event body must be a JSON object")
    service, endpoint = payload.get("service"), payload.get("endpoint")
    message, status_code = payload.get("error") or payload.get("message") or "", payload.get("status_code")
    if not isinstance(service, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", service):
        raise ValueError("service must be 1-80 letters, numbers, dots, underscores or hyphens")
    if not isinstance(endpoint, str) or not endpoint.startswith("/") or len(endpoint) > 256:
        raise ValueError("endpoint must be a path starting with / (max 256 characters)")
    if isinstance(status_code, bool) or not isinstance(status_code, int) or not 100 <= status_code <= 599:
        raise ValueError("status_code must be an integer from 100 to 599")
    if not isinstance(message, str) or len(message) > 2000:
        raise ValueError("error/message must be a string (max 2000 characters)")
    message = SECRET_PATTERN.sub(r"\1=[REDACTED]", message).strip() or f"HTTP {status_code}"
    error_type = classify_error(message)
    safe_payload = {"service": service, "endpoint": endpoint, "status_code": status_code,
                    "message": message, "trace_id": str(payload.get("trace_id", ""))[:128]}
    with connect() as db:
        if status_code < 500:
            return {"event": add_event(db, "log_received", safe_payload), "incident": None, "grouped": False}
        fingerprint = hashlib.sha256(f"{service}\0{endpoint}\0{error_type}\0{message}".encode()).hexdigest()
        existing = db.execute(
            "SELECT * FROM incidents WHERE fingerprint = ? AND status != 'resolved' ORDER BY created_at DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        occurred_at, grouped = now_iso(), existing is not None
        if existing:
            incident_id = existing["id"]
            db.execute("UPDATE incidents SET occurrence_count = occurrence_count + 1, updated_at = ? WHERE id = ?",
                       (occurred_at, incident_id))
        else:
            incident_id = f"DP-{uuid.uuid4().hex[:8].upper()}"
            db.execute("INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)", (
                incident_id, service, endpoint, "critical" if status_code >= 503 else "high", "open",
                error_type, message, fingerprint, occurred_at, occurred_at,
            ))
        event = add_event(db, "related_error_detected" if grouped else "error_detected", safe_payload, incident_id)
        return {"event": event, "incident": get_incident(db, incident_id), "grouped": grouped}


def investigate(incident_id: str) -> dict:
    with connect() as db:
        incident = get_incident(db, incident_id)
        add_event(db, "investigation_started", {"summary": "Rule-based evidence review started"}, incident_id)
        rows = db.execute(
            "SELECT occurred_at, payload FROM events WHERE incident_id = ? AND event_type IN "
            "('error_detected', 'related_error_detected', 'log_received') ORDER BY occurred_at DESC LIMIT 20",
            (incident_id,),
        ).fetchall()
        evidence = [{"occurred_at": row["occurred_at"], **json.loads(row["payload"])} for row in rows]
        add_event(db, "evidence_collected", {"summary": f"Collected {len(evidence)} related error event(s)", "event_count": len(evidence)}, incident_id)
        if incident["error_type"] == "timeout":
            cause = "A timeout is present in the reported error. Upstream latency or a saturated dependency may be involved."
        elif incident["error_type"] == "database_error":
            cause = "The error mentions a database or connection pool. Database availability and pool saturation are plausible causes."
        else:
            cause = "The event confirms an HTTP 5xx, but does not identify its underlying cause. Inspect service traces and application logs."
        confidence = 0.55 if incident["error_type"] in ("timeout", "database_error") else 0.25
        add_event(db, "hypothesis_identified", {"summary": "A rule-based hypothesis was recorded; cause is not confirmed", "confidence": confidence}, incident_id)
        db.execute("INSERT INTO investigations VALUES (?, ?, ?, ?, ?) ON CONFLICT(incident_id) DO UPDATE SET "
                   "confidence=excluded.confidence, root_cause=excluded.root_cause, evidence=excluded.evidence, updated_at=excluded.updated_at",
                   (incident_id, confidence, cause, json.dumps(evidence), now_iso()))
        return get_incident(db, incident_id)


def generate_test(incident_id: str) -> dict:
    with connect() as db:
        incident = get_incident(db, incident_id)
        if incident["investigation"] is None:
            raise ValueError("Run an investigation before generating a test proposal")
        test = {"id": f"TC-{uuid.uuid4().hex[:8].upper()}", "incident_id": incident_id,
                "title": f"Validate {incident['error_type']} detection",
                "description": f"Verify that the captured {incident['service']} event for {incident['endpoint']} is classified as an HTTP 5xx incident. This detector check does not reproduce an application fault.",
                "created_at": now_iso(), "result": None}
        db.execute("INSERT INTO test_cases VALUES (?, ?, ?, ?, ?, NULL)",
                   (test["id"], incident_id, test["title"], test["description"], test["created_at"]))
        add_event(db, "test_generated", {"summary": test["title"], "test_id": test["id"]}, incident_id)
        return test


def execute_test(test_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT test_cases.*, incidents.error_message FROM test_cases JOIN incidents "
                         "ON incidents.id = test_cases.incident_id WHERE test_cases.id = ?", (test_id,)).fetchone()
        if row is None:
            raise NotFound("Test case not found")
        passed = bool(row["error_message"].strip())
        result = {"status": "passed" if passed else "failed",
                  "summary": "Captured error evidence is present" if passed else "Captured error evidence is empty",
                  "executed_at": now_iso(), "scope": "Detector evidence check only; the monitored application was not executed."}
        db.execute("UPDATE test_cases SET result = ? WHERE id = ?", (json.dumps(result), test_id))
        add_event(db, "test_executed", {"summary": result["summary"], "test_id": test_id}, row["incident_id"])
        return {**dict(row), "result": result}


def create_report(incident_id: str) -> dict:
    with connect() as db:
        incident = get_incident(db, incident_id)
        tests = db.execute("SELECT * FROM test_cases WHERE incident_id = ? ORDER BY created_at", (incident_id,)).fetchall()
        report_id, created_at = f"RP-{uuid.uuid4().hex[:8].upper()}", now_iso()
        body = {
            "report_id": report_id, "generated_at": created_at,
            "incident": {key: incident[key] for key in ("id", "service", "endpoint", "severity", "status", "error_type", "error_message", "occurrence_count", "created_at")},
            "investigation": incident["investigation"], "timeline": incident["events"],
            "tests": [{**dict(row), "result": json.loads(row["result"]) if row["result"] else None} for row in tests],
            "disclaimer": "Rule-based investigation output. Suspected causes require developer verification.",
        }
        db.execute("INSERT INTO reports VALUES (?, ?, ?, ?)", (report_id, incident_id, created_at, json.dumps(body)))
        add_event(db, "report_generated", {"summary": "Incident report generated", "report_id": report_id}, incident_id)
        body["timeline"] = get_incident(db, incident_id)["events"]
        db.execute("UPDATE reports SET body = ? WHERE id = ?", (json.dumps(body), report_id))
        return {"id": report_id, "incident_id": incident_id, "created_at": created_at, "body": body}


def overview() -> dict:
    with connect() as db:
        incidents = [dict(row) for row in db.execute("SELECT * FROM incidents ORDER BY created_at DESC LIMIT 100").fetchall()]
        events = [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute(
            "SELECT id, incident_id, event_type AS type, occurred_at, payload FROM events ORDER BY occurred_at DESC, rowid DESC LIMIT 8"
        ).fetchall()]
        services = []
        for name in SERVICE_NAMES:
            observed = db.execute("SELECT COUNT(*) AS count, MAX(occurred_at) AS last_seen FROM events WHERE json_extract(payload, '$.service') = ?", (name,)).fetchone()
            active = sum(item["service"] == name and item["status"] != "resolved" for item in incidents)
            services.append({"name": name, "status": "degraded" if active else "healthy" if observed["count"] else "unknown",
                             "events": observed["count"], "last_seen": observed["last_seen"]})
        return {"incident_count": len(incidents), "active_count": sum(item["status"] != "resolved" for item in incidents),
                "service_count": len(services), "services": services, "incidents": incidents, "recent_events": events}


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, data: object, content_type: str = "application/json") -> None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 1 or length > 65536:
            raise ValueError("Request body must be between 1 and 65536 bytes")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request body must be valid JSON") from exc

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/overview":
                return self.respond(200, overview())
            if path == "/api/incidents":
                with connect() as db:
                    return self.respond(200, [dict(row) for row in db.execute("SELECT * FROM incidents ORDER BY created_at DESC").fetchall()])
            if path == "/api/services":
                return self.respond(200, overview()["services"])
            if path == "/api/reports":
                with connect() as db:
                    return self.respond(200, [dict(row) for row in db.execute("SELECT id, incident_id, created_at FROM reports ORDER BY created_at DESC").fetchall()])
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "incidents"] and parts[3] == "tests":
                with connect() as db:
                    if db.execute("SELECT 1 FROM incidents WHERE id = ?", (parts[2],)).fetchone() is None:
                        raise NotFound("Incident not found")
                    rows = db.execute("SELECT * FROM test_cases WHERE incident_id = ? ORDER BY created_at", (parts[2],)).fetchall()
                    return self.respond(200, [{**dict(row), "result": json.loads(row["result"]) if row["result"] else None} for row in rows])
            if len(parts) == 3 and parts[:2] == ["api", "incidents"]:
                with connect() as db:
                    return self.respond(200, get_incident(db, parts[2]))
            if len(parts) == 3 and parts[:2] == ["api", "reports"]:
                with connect() as db:
                    row = db.execute("SELECT body FROM reports WHERE id = ?", (parts[2],)).fetchone()
                    if row is None:
                        raise NotFound("Report not found")
                    return self.respond(200, json.loads(row["body"]))
            if path == "/" or path.startswith("/assets/"):
                asset_name = path.rsplit("/", 1)[-1]
                file_path = (FRONTEND / ("index.html" if path == "/" else asset_name)).resolve()
                if not file_path.is_relative_to(FRONTEND.resolve()) or not file_path.is_file():
                    raise NotFound("File not found")
                content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(file_path.suffix, "application/octet-stream")
                return self.respond(200, file_path.read_bytes(), content_type)
            raise NotFound("Route not found")
        except NotFound as exc:
            self.respond(404, {"error": str(exc)})
        except Exception:
            self.respond(500, {"error": "Internal server error"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/events":
                return self.respond(201, ingest_event(body))
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "incidents"]:
                incident_id, action = parts[2], parts[3]
                if action == "investigate":
                    return self.respond(200, investigate(incident_id))
                if action == "tests" and body.get("action") == "generate":
                    return self.respond(201, generate_test(incident_id))
                if action == "resolve":
                    with connect() as db:
                        get_incident(db, incident_id)
                        db.execute("UPDATE incidents SET status='resolved', updated_at=? WHERE id=?", (now_iso(), incident_id))
                        add_event(db, "incident_resolved", {"summary": "Incident marked resolved"}, incident_id)
                        return self.respond(200, get_incident(db, incident_id))
                if action == "reports":
                    return self.respond(201, create_report(incident_id))
            if len(parts) == 4 and parts[:2] == ["api", "tests"] and parts[3] == "execute":
                return self.respond(200, execute_test(parts[2]))
            raise NotFound("Route not found")
        except ValueError as exc:
            self.respond(400, {"error": str(exc)})
        except NotFound as exc:
            self.respond(404, {"error": str(exc)})
        except Exception:
            self.respond(500, {"error": "Internal server error"})

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{now_iso()}] {self.address_string()} {format_string % args}")


def main() -> None:
    initialize_database()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("DebugPilot listening at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DebugPilot")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()