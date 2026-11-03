const state = { incidents: [], selectedIncident: "", filter: "all", toastTimer: null };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const pageNames = { overview: "Overview", services: "Services", incidents: "Incidents", investigation: "Investigation", tests: "Test results", reports: "Reports", settings: "Settings" };

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...options.headers } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function escapeHtml(value = "") { return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
function formatTime(value) { return value ? `${new Intl.DateTimeFormat(undefined, { timeStyle: "medium", hour12: false, timeZone: "UTC" }).format(new Date(value))} UTC` : "--"; }
function relativeTime(value) { if (!value) return "--"; const s = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000)); return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`; }
function toast(message) { const element = $("#toast"); element.textContent = message; element.classList.add("show"); clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => element.classList.remove("show"), 3000); }

function setPage(name) {
  if (!pageNames[name]) name = "overview";
  $$(".page").forEach((page) => page.classList.toggle("active", page.id === `page-${name}`));
  $$(".nav-link").forEach((link) => link.classList.toggle("active", link.dataset.page === name));
  $("#topbar-page").textContent = pageNames[name];
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "investigation") renderInvestigation();
  if (name === "tests") loadTests();
  if (name === "reports") loadReports();
}
function statusPill(status) { return `<span class="health-pill ${escapeHtml(status)}"><span class="status-dot ${escapeHtml(status)}"></span>${escapeHtml(status === "unknown" ? "Awaiting data" : status)}</span>`; }

function renderServices(services) {
  $("#service-nav-count").textContent = services.length;
  $("#metric-services").textContent = services.length;
  $("#service-total").textContent = `${services.length} services`;
  $("#overview-services").innerHTML = services.map((s) => `<div class="service-row"><span class="service-symbol">◈</span><span class="service-name">${escapeHtml(s.name)}</span><span class="service-detail"><span class="status-dot ${escapeHtml(s.status)}"></span><span class="status-text">${escapeHtml(s.status === "unknown" ? "Awaiting data" : s.status)}</span><span>·</span>${s.events} events</span></div>`).join("");
  $("#services-table").innerHTML = services.map((s) => `<tr><td><span class="table-primary">${escapeHtml(s.name)}</span><span class="table-sub">Application service</span></td><td>${statusPill(s.status)}</td><td class="mono">${s.events}</td><td class="mono">${s.last_seen ? formatTime(s.last_seen) : "No events yet"}</td><td><button class="row-action service-event" data-service="${escapeHtml(s.name)}">Send event</button></td></tr>`).join("");
}
function renderIncidents(incidents) {
  state.incidents = incidents;
  $("#incident-nav-count").textContent = incidents.filter((i) => i.status !== "resolved").length;
  const shown = incidents.filter((i) => state.filter === "all" || (state.filter === "active" && i.status !== "resolved") || (state.filter === "resolved" && i.status === "resolved"));
  $("#incident-filter-meta").textContent = `${shown.length} incident${shown.length === 1 ? "" : "s"}`;
  $("#incidents-table").innerHTML = shown.length ? shown.map((i) => `<tr><td><button class="incident-link" data-open-incident="${escapeHtml(i.id)}">${escapeHtml(i.id)}</button><span class="table-sub">${escapeHtml(i.error_message)}</span></td><td><span class="table-primary">${escapeHtml(i.service)}</span><span class="table-sub mono">${escapeHtml(i.endpoint)}</span></td><td><span class="severity-badge ${escapeHtml(i.severity)}">${escapeHtml(i.severity)}</span></td><td class="mono">${i.occurrence_count}</td><td>${statusPill(i.status === "resolved" ? "healthy" : "degraded")}</td><td class="mono">${formatTime(i.created_at)}</td></tr>`).join("") : `<tr><td colspan="6"><div class="empty-inline">No incidents match this filter.</div></td></tr>`;
  $("#overview-incidents").innerHTML = incidents.slice(0, 3).map((i) => `<button class="compact-incident" data-open-incident="${escapeHtml(i.id)}"><span class="incident-mark"></span><span><span class="compact-title">${escapeHtml(i.error_message)}</span><span class="compact-subtitle">${escapeHtml(i.service)} · ${i.occurrence_count} occurrence${i.occurrence_count === 1 ? "" : "s"}</span></span><span class="compact-time">${relativeTime(i.updated_at)}</span></button>`).join("") || `<div class="empty-inline">No incidents recorded. Send a service event to begin.</div>`;
  $("#investigation-select").innerHTML = `<option value="">Select an incident</option>` + incidents.map((i) => `<option value="${escapeHtml(i.id)}">${escapeHtml(i.id)} · ${escapeHtml(i.service)} · ${escapeHtml(i.error_type)}</option>`).join("");
  if (state.selectedIncident && incidents.some((i) => i.id === state.selectedIncident)) $("#investigation-select").value = state.selectedIncident;
}

const eventLabels = { error_detected: "Error detected", related_error_detected: "Related error grouped", investigation_started: "Investigation started", evidence_collected: "Related evidence collected", hypothesis_identified: "Possible root cause identified", test_generated: "Test proposal generated", test_executed: "Detector check executed", report_generated: "Report generated", incident_resolved: "Incident marked resolved" };
async function renderInvestigation() {
  const id = state.selectedIncident || $("#investigation-select").value;
  if (!id) { $("#investigation-empty").hidden = false; $("#investigation-content").hidden = true; return; }
  try {
    const incident = await api(`/api/incidents/${encodeURIComponent(id)}`);
    state.selectedIncident = id; $("#investigation-select").value = id;
    $("#investigation-empty").hidden = true; $("#investigation-content").hidden = false;
    $("#investigation-id").textContent = incident.id;
    $("#investigation-severity").textContent = incident.severity;
    $("#investigation-severity").className = `severity-badge ${incident.severity}`;
    $("#investigation-error").textContent = incident.error_message;
    const sourceEvent = incident.events.find((e) => e.payload.status_code >= 500);
    $("#investigation-route").textContent = `${incident.endpoint} · HTTP ${sourceEvent?.payload.status_code || "5xx"}`;
    $("#investigation-service").textContent = incident.service;
    $("#investigation-occurrences").textContent = incident.occurrence_count;
    $("#investigation-created").textContent = formatTime(incident.created_at);
    $("#investigation-status").textContent = incident.status;
    $("#investigate-button").disabled = incident.status === "resolved";
    $("#investigate-button").textContent = incident.investigation ? "Run investigation again" : "Start investigation";
    $("#resolve-incident").hidden = incident.status === "resolved";
    $("#investigation-timeline").innerHTML = incident.events.map((e) => `<div class="timeline-item"><time class="timeline-time" datetime="${escapeHtml(e.occurred_at)}">${formatTime(e.occurred_at)}</time><span class="timeline-rail"><i class="timeline-dot ${e.type === "error_detected" ? "important" : ""}"></i></span><div class="timeline-copy"><strong>${escapeHtml(eventLabels[e.type] || e.type.replaceAll("_", " "))}</strong><p>${escapeHtml(e.payload.summary || e.payload.message || `${e.payload.event_count ?? ""} recorded event(s)`)}</p></div></div>`).join("") || `<div class="empty-inline">No events recorded for this incident.</div>`;
    $("#timeline-count").textContent = `${incident.events.length} EVENT${incident.events.length === 1 ? "" : "S"}`;
    $("#evidence-json").textContent = JSON.stringify(sourceEvent ? { recorded_at: sourceEvent.occurred_at, ...sourceEvent.payload } : {}, null, 2);
    const investigation = incident.investigation;
    $("#root-cause-title").textContent = investigation ? investigation.root_cause : "Investigation not started";
    $("#root-cause-copy").textContent = investigation ? `${investigation.evidence.length} related event(s) reviewed. This is a suspected cause, not a confirmed diagnosis.` : "Start an investigation to review the error signal. Findings remain suspected until verified by a developer.";
    $("#confidence-value").textContent = investigation ? `${Math.round(investigation.confidence * 100)}%` : "--";
    $("#confidence-track-fill").style.width = investigation ? `${Math.round(investigation.confidence * 100)}%` : "0";
  } catch (error) { toast(error.message); }
}

async function refresh() {
  try {
    const data = await api("/api/overview");
    renderServices(data.services); renderIncidents(data.incidents);
    $("#metric-total").textContent = data.incident_count;
    $("#metric-active").textContent = data.active_count;
    const observed = data.services.filter((s) => s.status !== "unknown").length;
    const degraded = data.services.filter((s) => s.status === "degraded").length;
    $("#health-summary").textContent = !observed ? "Awaiting events" : degraded ? `${degraded} service${degraded === 1 ? "" : "s"} degraded` : "All observed healthy";
    $("#health-detail").textContent = !observed ? "No service signals received yet" : `${observed} of ${data.services.length} services observed`;
    $("#overview-events").innerHTML = data.recent_events.map((e) => `<div class="activity-item"><span class="activity-mark"></span><div class="activity-description"><strong>${escapeHtml(eventLabels[e.type] || e.type.replaceAll("_", " "))}</strong> · ${escapeHtml(e.payload.service || e.payload.summary || "DebugPilot event")}${e.incident_id ? ` · <button class="incident-link" data-open-incident="${escapeHtml(e.incident_id)}">${escapeHtml(e.incident_id)}</button>` : ""}</div><time class="activity-time">${formatTime(e.occurred_at)}</time></div>`).join("") || `<div class="empty-inline">Activity appears here when events arrive.</div>`;
    $("#last-refreshed").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    $("#api-status").textContent = "Connected · SQLite";
  } catch { $("#api-status").textContent = "Offline · start backend"; $("#health-summary").textContent = "API unavailable"; $("#health-detail").textContent = "Run python3 backend/main.py to connect"; }
}
async function loadTests() {
  const groups = await Promise.all(state.incidents.map(async (i) => { try { return await api(`/api/incidents/${encodeURIComponent(i.id)}/tests`); } catch { return []; } }));
  const tests = groups.flat().sort((a, b) => b.created_at.localeCompare(a.created_at));
  $("#test-total").textContent = `${tests.length} check${tests.length === 1 ? "" : "s"}`;
  $("#tests-table").innerHTML = tests.length ? tests.map((t) => `<tr><td><span class="table-primary">${escapeHtml(t.title)}</span><span class="table-sub">${escapeHtml(t.description)}</span></td><td><button class="incident-link" data-open-incident="${escapeHtml(t.incident_id)}">${escapeHtml(t.incident_id)}</button></td><td class="mono">${formatTime(t.created_at)}</td><td><span class="result-pill">${escapeHtml(t.result?.status || "Not executed")}</span></td><td class="mono">${t.result ? formatTime(t.result.executed_at) : "--"}</td></tr>`).join("") : `<tr><td colspan="5"><div class="empty-inline">No checks yet. Start an investigation and generate a detector check.</div></td></tr>`;
}
async function loadReports() {
  try {
    const reports = await api("/api/reports");
    $("#report-total").textContent = `${reports.length} report${reports.length === 1 ? "" : "s"}`;
    $("#reports-table").innerHTML = reports.length ? reports.map((r) => `<tr><td><button class="incident-link download-report" data-report="${escapeHtml(r.id)}">${escapeHtml(r.id)}</button></td><td><button class="incident-link" data-open-incident="${escapeHtml(r.incident_id)}">${escapeHtml(r.incident_id)}</button></td><td class="mono">${formatTime(r.created_at)}</td><td><span class="result-pill">JSON</span></td><td><button class="row-action download-report" data-report="${escapeHtml(r.id)}">Download</button></td></tr>`).join("") : `<tr><td colspan="5"><div class="empty-inline">No reports generated yet.</div></td></tr>`;
  } catch (error) { toast(error.message); }
}
function openEventDialog(service = "payment-service") {
  const form = $("#event-form"); form.elements.service.value = service;
  form.elements.endpoint.value = service === "user-service" ? "/api/users" : service === "product-service" ? "/api/products" : "/api/payments";
  $("#event-dialog").showModal();
}
async function submitEvent(form) {
  const f = new FormData(form);
  const result = await api("/api/events", { method: "POST", body: JSON.stringify({ service: f.get("service"), endpoint: f.get("endpoint"), status_code: Number(f.get("status_code")), error: f.get("error"), trace_id: f.get("trace_id") }) });
  $("#event-dialog").close();
  if (result.incident) { state.selectedIncident = result.incident.id; toast(result.grouped ? `Related error grouped into ${result.incident.id}` : `Incident ${result.incident.id} recorded`); }
  else toast("Service event recorded");
  await refresh();
  if (result.incident) { setPage("investigation"); await renderInvestigation(); }
}
async function downloadReport(id) {
  const report = await api(`/api/reports/${encodeURIComponent(id)}`);
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  const anchor = Object.assign(document.createElement("a"), { href: url, download: `${id}.json` }); anchor.click(); URL.revokeObjectURL(url); toast(`Downloaded ${id}`);
}

document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-page]");
  if (nav) { event.preventDefault(); setPage(nav.dataset.page); return; }
  if (event.target.closest("#open-event, #empty-send-event")) { openEventDialog(); return; }
  const serviceEvent = event.target.closest(".service-event"); if (serviceEvent) { openEventDialog(serviceEvent.dataset.service); return; }
  const incident = event.target.closest("[data-open-incident]"); if (incident) { state.selectedIncident = incident.dataset.openIncident; setPage("investigation"); await renderInvestigation(); return; }
  if (event.target.closest("#close-event")) { $("#event-dialog").close(); return; }
  if (event.target.closest("#refresh-services, #refresh-incidents")) { await refresh(); toast("Workspace refreshed"); return; }
  const filter = event.target.closest("[data-filter]"); if (filter) { state.filter = filter.dataset.filter; $$(".segment").forEach((b) => b.classList.toggle("active", b === filter)); renderIncidents(state.incidents); return; }
  if (event.target.closest("#investigate-button")) {
    try { await api(`/api/incidents/${encodeURIComponent(state.selectedIncident)}/investigate`, { method: "POST", body: "{}" }); await refresh(); await renderInvestigation(); toast("Investigation events recorded"); } catch (e) { toast(e.message); } return;
  }
  if (event.target.closest("#generate-test")) {
    try { const test = await api(`/api/incidents/${encodeURIComponent(state.selectedIncident)}/tests`, { method: "POST", body: JSON.stringify({ action: "generate" }) }); await refresh(); await renderInvestigation(); toast(`Check ${test.id} generated`); } catch (e) { toast(e.message); } return;
  }
  if (event.target.closest("#execute-test")) {
    try { const tests = await api(`/api/incidents/${encodeURIComponent(state.selectedIncident)}/tests`); if (!tests.length) { toast("Generate a detector check first"); return; } const result = await api(`/api/tests/${encodeURIComponent(tests.at(-1).id)}/execute`, { method: "POST", body: "{}" }); await refresh(); await renderInvestigation(); toast(`Built-in evidence check ${result.result.status}`); } catch (e) { toast(e.message); } return;
  }
  if (event.target.closest("#generate-report")) {
    try { const report = await api(`/api/incidents/${encodeURIComponent(state.selectedIncident)}/reports`, { method: "POST", body: "{}" }); await refresh(); await downloadReport(report.id); await renderInvestigation(); } catch (e) { toast(e.message); } return;
  }
  if (event.target.closest("#resolve-incident")) {
    try { await api(`/api/incidents/${encodeURIComponent(state.selectedIncident)}/resolve`, { method: "POST", body: "{}" }); await refresh(); await renderInvestigation(); toast("Incident marked resolved"); } catch (e) { toast(e.message); } return;
  }
  const report = event.target.closest(".download-report"); if (report) { try { await downloadReport(report.dataset.report); } catch (e) { toast(e.message); } return; }
  if (event.target.closest("#copy-curl")) { try { await navigator.clipboard.writeText($(".code-sample").textContent); toast("Command copied"); } catch { toast("Clipboard unavailable in this browser"); } }
});

$("#event-form").addEventListener("submit", async (event) => { event.preventDefault(); try { await submitEvent(event.currentTarget); } catch (e) { toast(e.message); } });
$("#investigation-select").addEventListener("change", async (event) => { state.selectedIncident = event.target.value; await renderInvestigation(); });
window.addEventListener("hashchange", () => setPage(location.hash.slice(1)));
setPage(pageNames[location.hash.slice(1)] ? location.hash.slice(1) : "overview");
refresh();
setInterval(refresh, 15000);