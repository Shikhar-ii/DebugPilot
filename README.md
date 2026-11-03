# DebugPilot

DebugPilot is a local-first incident investigation MVP. It accepts structured events from running applications, groups repeated HTTP 5xx errors, records evidence and investigation actions in SQLite, and provides a dashboard for reviewing incidents and exporting reports.

The current investigation uses transparent rules, not an AI model. Findings are labeled as hypotheses. Its generated check validates captured detector evidence; it does not execute generated code or reproduce a fault inside the monitored application.

## Run locally

Requires Python 3.10 or later. No third-party packages are required.

```bash
python3 backend/main.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The API binds to localhost and stores its database at `backend/debugpilot.sqlite3`.

## Send an event

```bash
curl -X POST http://127.0.0.1:8000/api/events \
	-H 'Content-Type: application/json' \
	-d '{
		"service": "payment-service",
		"endpoint": "/api/payments",
		"status_code": 500,
		"error": "Database connection timeout",
		"trace_id": "trace-123"
	}'
```

The API assigns UTC timestamps when it records events. HTTP 5xx events create or group active incidents; other status codes are stored as ordinary service events. Common inline password, token, secret, and API key assignments in the message are redacted before storage.

## MVP pages

- **Overview:** incident counts, observed service health, and recent event activity.
- **Services:** the three example service names and their observed event intake.
- **Incidents:** grouped 5xx incidents, occurrence counts, severity, and resolution status.
- **Investigation:** actual event timestamps, captured input, evidence, and a rule-based suspected cause.
- **Test results:** generated detector-check proposals and built-in evidence-check outcomes.
- **Reports:** downloadable JSON incident reports.
- **Settings:** event API details and monitoring rule behavior.

The timeline contains only events actually recorded by the API. Investigation actions add their own events when run; no progress timestamps are fabricated.

## API

- `POST /api/events` accepts an event with `service`, `endpoint`, `status_code`, and optional `error`, `message`, and `trace_id`.
- `GET /api/overview`, `GET /api/services`, and `GET /api/incidents` return dashboard data.
- `GET /api/incidents/{id}` returns an incident and its event timeline.
- `POST /api/incidents/{id}/investigate` records evidence review and a suspected cause.
- `POST /api/incidents/{id}/tests` with `{"action":"generate"}` creates a detector check.
- `POST /api/tests/{id}/execute` runs the built-in evidence-presence check.
- `POST /api/incidents/{id}/reports` generates a report; `GET /api/reports` lists reports and `GET /api/reports/{id}` retrieves one.
- `POST /api/incidents/{id}/resolve` marks an incident resolved.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Scope and security

This is a development MVP, not a production monitoring or autonomous debugging system. It receives pushed events but does not install service agents, collect traces, connect an LLM, authenticate API callers, or run application-level tests. The built-in check does not execute generated code. Keep the server on localhost; do not expose this unauthenticated API to a network.
