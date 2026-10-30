"use strict";
const $ = id => document.getElementById(id);
// Browser privacy settings can disable storage; the workbench must still open.
const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch {} }
};
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let state = {projects:[],jobs:[],settings:{}}, selectedProject = null, projectInfo = null;
let selectedJob = null, jobInfo = null, taskMode = "fix", cloud = false, activeTab = "changes";
let uploadId = null, importProject = null, toastTimer, busy = false;
let plans = [], selectedPlan = null, planInfo = null, planUploadJob = null;
let checkEditor = null;
let renderedPlanKey = null;
let checkpointUpload = null;
let projectSelection = 0, projectLoading = false;
const activeStates = ["queued","running","cancelling"];
const title = p => p?.demo ? "Calculator demo" : p?.title || "Choose a project";

async function api(path, options = {}) {
  const response = await fetch(path, {cache:"no-store", ...options,
    headers:{"X-Aegaeon-Token":window.AEGAEON_TOKEN, ...(options.body && typeof options.body === "string" ? {"Content-Type":"application/json"}:{}), ...options.headers}});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}
const post = (path, value) => api(path,{method:"POST",body:JSON.stringify(value)});
function toast(message, error = false) {
  const modal = Array.from(document.querySelectorAll("dialog[open]")).at(-1);
  if (error && modal) {
    let notice = modal.querySelector(".dialog-error");
    if (!notice) { notice = document.createElement("p"); notice.className = "dialog-error plan-error"; notice.setAttribute("role","alert"); modal.prepend(notice); }
    notice.textContent = message; notice.scrollIntoView({block:"nearest"});
    return;
  }
  clearTimeout(toastTimer); $("toast").textContent = message;
  $("toast").className = "toast" + (error ? " error" : "");
  toastTimer = setTimeout(() => $("toast").classList.add("hidden"), error ? 9000 : 6000);
}
function act(fn) { return async event => { try { await fn(event); } catch(error) { toast(error.message,true); } }; }
function dialog(id) { $(id).querySelector(".dialog-error")?.remove(); $(id).showModal(); }
function project() { return state.projects.find(p => p.id === selectedProject); }

function renderProjects() {
  $("project-list").innerHTML = state.projects.length ? state.projects.map(p => `<button class="project-item ${p.id === selectedProject ? "active" : ""}" data-project="${p.id}"><span class="project-dot"></span><span class="project-name">${escapeHTML(title(p))}</span>${p.demo ? "<small>DEMO</small>" : ""}</button>`).join("") : '<p class="muted">No projects yet. Open a folder or try the demo.</p>';
  $("session-count").textContent = state.jobs.length;
}
async function selectProject(identifier) {
  const selection = ++projectSelection;
  projectLoading = true; renderTransport();
  try {
  const info = await api("/api/project?id=" + encodeURIComponent(identifier));
  if (selection !== projectSelection) return;
  selectedProject = identifier; selectedJob = null; jobInfo = null;
  selectedPlan = storage.get("aegaeon-plan-" + identifier); planInfo = null;
  storage.set("aegaeon-project", identifier);
  projectInfo = info;
  cloud = false;
  $("request").value = projectInfo.demo ? "Fix add in calc.py so it adds numbers correctly. Preserve the existing regression tests." : "";
  $("request").readOnly = Boolean(projectInfo.demo);
  taskMode = "fix";
  document.querySelectorAll("[data-mode]").forEach(button => {
    button.disabled = Boolean(projectInfo.demo && button.dataset.mode !== "fix");
    button.classList.toggle("active", button.dataset.mode === "fix");
  });
  $("scope").value = projectInfo.demo ? "calc.py" : "**";
  $("checks").value = JSON.stringify(projectInfo.checks, null, 2);
  $("allow-checks").checked = Boolean(projectInfo.demo);
  $("allow-test-edits").checked = false;
  $("plan-checks").checked = Boolean(projectInfo.demo);
  $("plan-run-checks").checked = Boolean(projectInfo.demo);
  $("plan-manual").checked = false;
  $("checkpoint-history").classList.toggle("hidden", !projectInfo.recovery);
  $("breadcrumb-project").textContent = title(projectInfo);
  $("project-tag").textContent = projectInfo.demo ? "OFFLINE DEMO" : "GIT PROJECT";
  $("context-title").textContent = title(projectInfo);
  $("context-path").textContent = projectInfo.root;
  $("commit").textContent = projectInfo.commit;
  $("file-count").textContent = projectInfo.files.length;
  $("file-tree").innerHTML = projectInfo.files.slice(0,35).map(name => `<button data-file="${escapeHTML(name)}" title="${escapeHTML(name)}"><span>⌑</span>${escapeHTML(name)}</button>`).join("");
  $("demo-notice").classList.toggle("hidden", !projectInfo.demo);
  $("local-mode").querySelector("span").textContent = projectInfo.demo ? "Offline demo" : "Local model";
  renderProjects(); renderTransport(); await refreshJob(true);
  if (selection !== projectSelection) return;
  await refreshPlans();
  } catch(error) { if (selection === projectSelection) throw error; }
  finally { if (selection === projectSelection) { projectLoading = false; renderTransport(); } }
}
function renderTransport() {
  $("task-form").inert = projectLoading;
  $("plan-section").inert = projectLoading;
  $("local-mode").classList.toggle("active", !cloud);
  $("notebook-mode").classList.toggle("active", cloud);
  const active = state.active && !cloud;
  $("run-button").disabled = projectLoading || busy || !selectedProject || active;
  $("run-button").innerHTML = projectLoading ? 'Loading project…' : cloud ? 'Download task.zip <span>↓</span>' : active ? 'Session in progress <span>◷</span>' : 'Run session <span>↗</span>';
}
function getTask() {
  if (projectLoading) throw new Error("Wait for the selected project to finish loading.");
  let checks;
  try { checks = JSON.parse($("checks").value); } catch { throw new Error("Declared checks must be valid JSON. Open Task scope & checks to edit them."); }
  const request = $("request").value.trim();
  if (!request) throw new Error("Describe your task first.");
  return {request,mode:taskMode,checks,allowed_paths:$("scope").value.split("\n").map(s => s.trim()).filter(Boolean),
    max_actions:12,max_changed_files:3,allow_test_edits:$("allow-test-edits").checked};
}
async function refresh() {
  state = await api("/api/state"); renderProjects(); renderTransport();
  if (projectLoading) return;
  if (!selectedProject && state.projects.length) {
    const saved = storage.get("aegaeon-project");
    await selectProject(state.projects.some(p => p.id === saved) ? saved : state.projects[0].id);
  } else if (selectedProject) { await refreshJob(); await refreshPlans(); }
}
async function refreshJob(force = false) {
  const selection = projectSelection;
  const jobs = state.jobs.filter(j => j.project_id === selectedProject);
  if (!selectedJob || !jobs.some(j => j.id === selectedJob)) selectedJob = jobs[0]?.id || null;
  const item = jobs.find(j => j.id === selectedJob);
  if (item && (force || !jobInfo || jobInfo.id !== item.id || jobInfo.state !== item.state || activeStates.includes(item.state))) {
    const loaded = await api("/api/job?id=" + encodeURIComponent(item.id));
    if (selection !== projectSelection || selectedJob !== item.id) return;
    jobInfo = loaded;
  }
  if (!item) jobInfo = null;
  renderSession();
}
function statusClass(value) {
  const lower = String(value).toLowerCase();
  return lower.includes("passed") || lower === "answer" ? "good" : ["running","queued","cancelling"].includes(lower) ? "running" : ["failed","blocked","partial","cancelled","interrupted"].includes(lower) ? "bad" : "neutral";
}
function diffHTML(diff) {
  if (!diff) return '<div class="empty-state"><span class="empty-icon">⌘</span><h3>No source changes yet.</h3><p>Check Activity for progress, or start a focused task.</p><span class="empty-footer">Your original project stays untouched.</span></div>';
  const lines = diff.split("\n");
  return '<div class="diff-header">UNIFIED DIFF · PROPOSED SOURCE CHANGES</div><pre class="diff-lines">' + lines.slice(0,600).map(line => `<span class="diff-line ${line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@") ? "meta" : line.startsWith("+") ? "add" : line.startsWith("-") ? "remove" : ""}">${escapeHTML(line) || " "}</span>`).join("") + '</pre>' + (lines.length > 600 ? '<p class="hint">Preview limited to 600 lines. Download the complete result.</p>' : "");
}
function eventDescription(event) {
  if (event.type === "tool") {
    const words = {read:"Read source",edit:"Updated source",list:"Listed source files",search:"Searched code",check:"Ran a check",diff:"Inspected the diff"};
    return [words[event.action] || event.action,event.observation?.error || event.path || event.observation?.status || "Completed"];
  }
  if (event.type === "verification") return ["Regression check", `${event.observation?.id}: ${event.observation?.status}`];
  if (event.type === "review") return ["Review completed",event.observation?.summary || "Review recorded"];
  if (event.type === "repair") return ["Repair attempt",`Attempt ${event.attempt} · guided by check evidence`];
  if (event.type === "session.finished") return ["Session finished",event.status];
  return [event.type.replaceAll("."," "),event.message || "Recorded"];
}
function renderSession() {
  const job = jobInfo;
  const status = job ? job.report?.status || job.state : "Ready when you are";
  $("run-state").textContent = status;
  $("run-state").className = "status-pill " + statusClass(status);
  $("session-title").textContent = job?.engine === "demo" ? "Offline demo session" : job?.engine === "notebook" ? "Notebook task" : "Your session workspace";
  $("cancel-button").classList.toggle("hidden", !job || !activeStates.includes(job.state));
  $("cancel-button").disabled = job?.state === "cancelling";
  $("cancel-button").textContent = job?.state === "cancelling" ? "Stopping…" : "Stop session";
  const changes = job?.diff ? (job.diff.match(/^\+\+\+ /gm) || []).length : 0;
  const events = job?.events || [], checks = job?.report?.checks || events.filter(e => e.type === "verification").map(e => e.observation);
  $("change-count").textContent = changes; $("event-count").textContent = events.length; $("check-count").textContent = checks.length;
  if (activeTab === "changes") $("session-content").innerHTML = diffHTML(job?.diff);
  if (activeTab === "activity") $("session-content").innerHTML = events.length ? '<div class="activity-list">' + events.map(e => {
    const [heading,detail] = eventDescription(e);
    return `<div class="event"><span class="event-icon">${e.type === "verification" ? "✓" : "·"}</span><div><strong>${escapeHTML(heading)}</strong><p>${escapeHTML(detail)}</p></div><time>${new Date(e.time*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}</time></div>`;
  }).join("") + '</div>' : '<div class="empty-state"><span class="empty-icon">◷</span><h3>Every step will be recorded.</h3><p>Reads, edits, checks, and reviews appear here as the session runs.</p></div>';
  if (activeTab === "checks") $("session-content").innerHTML = checks.length ? '<div class="checks-list">' + checks.map(c => `<div class="check-row"><div><strong>${escapeHTML(c.id)}</strong><span class="status-pill ${statusClass(c.status)}">${escapeHTML(c.status)}</span></div><code>${escapeHTML(c.argv?.join(" ") || c.reason || c.error || "Check recorded")}</code>${c.output ? `<pre>${escapeHTML(c.output)}</pre>` : ""}</div>`).join("") + '</div>' : '<div class="empty-state"><span class="empty-icon">✓</span><h3>Evidence, not guesswork.</h3><p>Enable declared checks to see real command outcomes.<br>A model’s conclusion is separate from test evidence.</p></div>';
  const finished = Boolean(job && (job.report || job.error || job.state === "exported"));
  $("result-footer").classList.toggle("hidden", !finished);
  $("result-summary").textContent = job?.branch ? `Review branch created: ${job.branch.branch}. Your canonical files remain unchanged.` : job?.error || job?.report?.summary || (job?.state === "exported" ? "Task packaged. Open the notebook to continue on cloud compute." : "");
  const assessment = job?.report?.assessment;
  let guidance = $("result-guidance");
  if (!guidance) { guidance = document.createElement("p"); guidance.id = "result-guidance"; guidance.className = "manual-notice"; $("result-footer").after(guidance); }
  guidance.hidden = !assessment;
  guidance.textContent = assessment ? `${assessment.title}: ${assessment.advice}` : "";
  $("download-result").classList.toggle("hidden", !(job?.report || job?.state === "exported"));
  $("download-result").textContent = job?.state === "exported" ? "Download task.zip" : "Download result.zip";
  $("download-result").href = job ? `/download/${job.id}/${job.state === "exported" ? "task" : "result"}` : "#";
  $("apply-button").classList.toggle("hidden", !job?.diff || Boolean(job?.branch) || Boolean(job?.plan));
}
async function checkConnection() {
  $("model-status").textContent = "Checking model…";
  const value = await api("/api/model");
  $("model-status").textContent = value.connected ? "Local model connected" : "Model not connected";
  $("connection-button").classList.toggle("connected", value.connected);
  $("connection-button").title = value.message || "Test your saved model connection in Model settings";
  return value;
}

$("project-list").addEventListener("click",act(async e => {
  const button = e.target.closest("[data-project]"); if (button) await selectProject(button.dataset.project);
}));
for (const id of ["add-project","open-folder-button"]) $(id).onclick = () => dialog("project-dialog");
$("project-form").onsubmit = act(async e => {
  e.preventDefault(); const value = await post("/api/projects",{path:$("project-path").value});
  $("project-dialog").close(); await refresh(); await selectProject(value.id); toast("Project opened. Your source stays on this computer.");
});
$("demo-button").onclick = act(async () => { const p = await post("/api/demo",{}); await refresh(); await selectProject(p.id); toast("Demo ready. Click Run session to try a real test-and-review workflow."); });
document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => b.closest("dialog").close());
document.querySelectorAll("[data-mode]").forEach(b => b.onclick = () => {
  taskMode = b.dataset.mode; document.querySelectorAll("[data-mode]").forEach(x => x.classList.toggle("active", x === b));
});
$("local-mode").onclick = () => { cloud = false; renderTransport(); };
$("notebook-mode").onclick = () => { cloud = true; renderTransport(); };
$("task-form").onsubmit = act(async e => {
  e.preventDefault(); if (!selectedProject) throw new Error("Open a project folder or try the offline demo first.");
  busy = true; renderTransport();
  try {
    const value = await post(cloud ? "/api/export" : "/api/run", {project_id:selectedProject,task:getTask(),allow_checks:$("allow-checks").checked});
    selectedJob = value.id; jobInfo = null; activeTab = cloud ? "changes" : "activity";
    document.querySelectorAll("[data-tab]").forEach(x => x.classList.toggle("active",x.dataset.tab === activeTab));
    await refresh(); if (cloud) { window.location.assign(value.download); toast("Task exported. Run it in your Colab or Kaggle notebook."); }
  } finally { busy = false; renderTransport(); }
});
$("cancel-button").onclick = act(async () => { const value = await post("/api/cancel",{job_id:selectedJob}); toast(value.message); await refresh(); });
document.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => {
  activeTab = b.dataset.tab; document.querySelectorAll("[data-tab]").forEach(x => x.classList.toggle("active", x === b)); renderSession();
});
$("apply-button").onclick = act(async () => {
  $("apply-button").disabled = true;
  try { const result = await post("/api/apply",{job_id:selectedJob}); toast(`Review branch created: ${result.branch}`); await refreshJob(true); }
  finally { $("apply-button").disabled = false; }
});
$("file-tree").onclick = act(async e => {
  const b = e.target.closest("[data-file]"); if (!b) return;
  const result = await api(`/api/file?id=${encodeURIComponent(selectedProject)}&path=${encodeURIComponent(b.dataset.file)}`);
  $("file-title").textContent = result.path; $("file-content").textContent = result.content; dialog("file-dialog");
});
$("settings-button").onclick = () => { $("endpoint").value = state.settings.endpoint; $("model-name").value = state.settings.model; $("context-tokens").value = state.settings.context; $("model-thinking").checked = Boolean(state.settings.thinking); dialog("settings-dialog"); };
$("probe-model").onclick = act(async () => {
  $("probe-model").disabled = true;
  $("probe-result").textContent = "Checking saved connection with a fixed prompt…";
  try {
    const result = await post("/api/model/probe", {});
    $("probe-result").textContent = `${result.status === "passed" ? "Passed" : "Failed"} · ${result.seconds}s. ${result.detail} ${result.notice}`;
  } finally { $("probe-model").disabled = false; }
});
$("settings-form").onsubmit = act(async e => {
  e.preventDefault(); await post("/api/settings",{endpoint:$("endpoint").value,model:$("model-name").value,context:Number($("context-tokens").value),thinking:$("model-thinking").checked});
  $("settings-dialog").close(); await refresh(); const connection = await checkConnection(); toast(connection.connected ? "Settings saved. Local model connected." : "Settings saved. Start your model server when ready.");
});
$("connection-button").onclick = act(async () => { const result = await checkConnection(); toast(result.connected ? "Local model endpoint is reachable." : result.message); });
for (const id of ["cloud-nav","cloud-guide-button"]) $(id).onclick = () => dialog("cloud-dialog");
$("choose-notebook-button").onclick = () => { $("cloud-dialog").close(); cloud = true; renderTransport(); $("request").focus(); };
$("workspace-nav").onclick = () => window.scrollTo({top:0,behavior:"smooth"});
$("history-nav").onclick = () => {
  $("history-list").innerHTML = state.jobs.length ? state.jobs.map(j => `<button class="history-item" data-history="${j.id}" data-project="${j.project_id}"><strong>${escapeHTML(j.task.request.slice(0,90))}</strong><small>${escapeHTML(j.report?.status || j.state)} · ${j.engine === "demo" ? "Simulated model / real checks" : escapeHTML(j.engine)} · ${new Date(j.created*1000).toLocaleString()}</small></button>`).join("") : '<p class="muted">No sessions yet. Start a task or export a notebook bundle.</p>';
  dialog("history-dialog");
};
$("history-list").onclick = act(async e => {
  const b = e.target.closest("[data-history]"); if (!b) return;
  await selectProject(b.dataset.project); selectedJob = b.dataset.history; await refreshJob(true); $("history-dialog").close();
});
$("import-button").onclick = () => {
  if (!selectedProject) { toast("Choose the project this result belongs to first.",true); return; }
  importProject = selectedProject; $("result-file").click();
};
$("result-file").onchange = act(async e => {
  const file = e.target.files[0]; if (!file) return;
  try {
    if (file.size > 25000000) throw new Error("Result ZIP exceeds the 25 MB upload limit.");
    const uploaded = await api("/api/upload",{method:"POST",body:file,headers:{"Content-Type":"application/zip"}}); uploadId = uploaded.upload_id;
    const preview = await post("/api/import",{upload_id:uploadId,project_id:importProject});
    $("import-summary").textContent = preview.summary || `${preview.files.length} changed source files`;
    $("import-diff").textContent = preview.diff || "No file changes in this result.";
    $("import-evidence").textContent = preview.evidence;
    $("confirm-import").disabled = !preview.files.length;
    dialog("import-dialog");
  } finally { e.target.value = ""; }
});
$("confirm-import").onclick = act(async () => {
  $("confirm-import").disabled = true;
  try { const result = await post("/api/import",{upload_id:uploadId,project_id:importProject,apply:true}); $("import-dialog").close(); toast(`Created ${result.branch}. Review it before merging.`); }
  finally { $("confirm-import").disabled = false; }
});

async function refreshPlans() {
  if (!selectedProject) return;
  const selection = projectSelection;
  const loaded = await api("/api/plans?project_id=" + encodeURIComponent(selectedProject));
  if (selection !== projectSelection) return;
  plans = loaded;
  if (!plans.some(p => p.id === selectedPlan)) selectedPlan = plans[0]?.id || null;
  planInfo = plans.find(p => p.id === selectedPlan) || null;
  if (selectedPlan) storage.set("aegaeon-plan-" + selectedProject, selectedPlan);
  $("plan-select").innerHTML = plans.length ? plans.map(p => `<option value="${p.id}">${escapeHTML(p.spec.goal.slice(0,65))} · ${escapeHTML(p.state)}</option>`).join("") : '<option value="">No saved plans</option>';
  $("plan-select").value = selectedPlan || "";
  renderPlan();
}
function renderPlan() {
  const p = planInfo;
  const key = JSON.stringify([p, state.active, busy, selectedProject]);
  if (key === renderedPlanKey) return;
  renderedPlanKey = key;
  const openDetails = new Set([...$("plan-board").querySelectorAll("details[data-plan-open][open]")].map(d => d.dataset.planOpen));
  $("plan-controls").classList.toggle("hidden", !p);
  $("new-plan").disabled = !selectedProject || busy || state.active;
  $("restore-checkpoint").disabled = state.active;
  if (!p) { $("plan-board").innerHTML = '<p class="muted">Start from the task below, or try the two-step offline demo.</p>'; return; }
  const accepted = new Set(p.spec.steps.filter(s => s.state === "accepted").map(s => s.id));
  const pending = p.spec.steps.some(s => s.run.job_id && !s.run.accepted && !["failed","cancelled","interrupted","abandoned"].includes(s.state));
  $("plan-board").innerHTML = `<div class="plan-summary"><strong>${escapeHTML(p.spec.goal)}</strong><span class="status-pill ${p.state === "complete" ? "good" : "neutral"}">${escapeHTML(p.state)} · ${accepted.size}/${p.spec.steps.length} reviewed</span></div>` +
    (p.error ? `<p class="plan-error" role="status">${escapeHTML(p.error)}</p>` : "") +
    (p.recovery ? `<p class="manual-notice">Recovered source includes ${p.recovery.accepted_steps.length} previously accepted step(s). This draft contains only remaining work. Prior reports are imported history, not fresh local verification.</p>` : "") +
    `<div class="plan-steps">${p.spec.steps.map(s => {
      const ready = p.state === "approved" && s.depends_on.every(id => accepted.has(id)) && s.state !== "accepted";
      const retry = ["failed","cancelled","interrupted","abandoned"].includes(s.state);
      const canRun = ready && (!pending || retry) && !state.active && !s.run.accepted && (!s.run.job_id || retry);
      const review = s.state === "finished" && !s.run.accepted && p.state === "approved";
      return `<article class="plan-step ${s.state === "accepted" ? "accepted" : ""}"><div class="plan-step-head"><span class="step-code">${escapeHTML(s.id)}</span><strong>${escapeHTML(s.title)}</strong><span class="status-pill ${s.state === "accepted" ? "good" : statusClass(s.state)}">${escapeHTML(s.state)}</span></div><p>${escapeHTML(s.acceptance)}</p><small>${s.depends_on.length ? "After " + escapeHTML(s.depends_on.join(", ")) : "No dependencies"} · ${escapeHTML(s.requirements.join(", "))} · ${escapeHTML(s.task.allowed_paths?.join(", ") || "**")}</small>
      ${policyHTML(s)}
      ${s.run.evidence ? `<p class="plan-evidence">${escapeHTML(s.run.evidence)} · ${escapeHTML(s.run.candidate?.commit?.slice(0,8) || p.base_commit.slice(0,8))}</p>` : ""}
      ${s.run.preflight?.status === "needs_setup" ? '<p class="plan-error">Acceptance needs local setup. Inspect the tool report before retrying.</p>' : ""}
      ${(s.run.verification || []).map(c => `<details class="plan-check" data-plan-open="${escapeHTML(s.id + ":verify:" + c.id)}"><summary>${escapeHTML(c.id)} · ${escapeHTML(c.status)}</summary><pre>${escapeHTML(c.output || c.reason || "")}</pre></details>`).join("")}
      <div class="plan-buttons">${canRun ? `<button class="button small primary" data-plan-run="${escapeHTML(s.id)}">${retry ? "Retry from checkpoint" : "Run step"}</button><button class="button small" data-plan-cloud="${escapeHTML(s.id)}">Export notebook task</button>` : ""}
      ${p.state === "draft" ? `<button class="button small" data-plan-policy="${escapeHTML(s.id)}">Configure checks</button>` : ""}<button class="button small" data-plan-setup="${escapeHTML(s.id)}">Check local setup</button>
      ${s.run.job_id ? `<button class="button small" data-plan-view="${s.run.job_id}">View attempt</button>` : ""}
      ${s.state === "exported" && p.state === "approved" ? `<a class="button small" href="/download/${s.run.job_id}/task">Download task.zip</a><button class="button small" data-plan-attach="${s.run.job_id}">Load result.zip</button>` : ""}
      ${review ? `<button class="button small primary" data-plan-accept="${s.run.job_id}" ${state.active ? "disabled" : ""}>Accept reviewed step</button>` : ""}
      ${s.run.job_id && !s.run.accepted && !activeStates.includes(s.state) && p.state === "approved" && s.state !== "abandoned" ? `<button class="button small" data-plan-abandon="${s.run.job_id}" ${state.active ? "disabled" : ""}>Abandon attempt</button>` : ""}</div></article>`;
    }).join("")}</div><div class="coverage"><strong>Requirement review</strong>${p.coverage.map(r => `<p><span>${r.reviewed ? "✓" : "○"}</span> ${escapeHTML(r.id)} · ${escapeHTML(r.text)} <small>(${escapeHTML(r.steps.join(", "))})</small></p>`).join("")}<small>Reviewed steps record human acceptance and command evidence. They do not prove every behavior.</small></div>`;
  const draft = p.state === "draft";
  $("plan-board").querySelectorAll("details[data-plan-open]").forEach(d => { if (openDetails.has(d.dataset.planOpen)) d.open = true; });
  $("edit-plan").classList.toggle("hidden", !draft);
  $("generate-plan").classList.toggle("hidden", !draft || Boolean(project()?.demo));
  $("approve-plan").classList.toggle("hidden", !draft);
  $("stop-plan").classList.toggle("hidden", p.state !== "verifying");
  $("approve-plan").disabled = state.active;
  $("generate-plan").disabled = state.active;
  $("review-plan").disabled = !accepted.size;
  $("backup-checkpoint").disabled = state.active;
}
$("plan-select").onchange = () => { selectedPlan = $("plan-select").value; planInfo = plans.find(p => p.id === selectedPlan); storage.set("aegaeon-plan-" + selectedProject,selectedPlan); renderPlan(); };
$("new-plan").onclick = act(async () => {
  if (!selectedProject) throw new Error("Open a project first.");
  const body = project()?.demo ? {project_id:selectedProject,demo:true} : {project_id:selectedProject,goal:$("request").value.trim(),checks:getTask().checks};
  const p = await post("/api/plan/create",body); selectedPlan = p.id; await refreshPlans();
  toast(project()?.demo ? "Two-step demo plan saved. Review and approve it to begin." : "Manual starting draft saved. Edit it or ask the local model to propose steps.");
  if (!project()?.demo) { $("plan-json").value = JSON.stringify(p.spec,null,2); dialog("plan-dialog"); }
});
function policyHTML(step) {
  const worker = step.task.checks || [], inherited = !Object.hasOwn(step,"acceptance_checks");
  const local = inherited ? worker : step.acceptance_checks;
  const commands = list => list.map(c => `<li><strong>${escapeHTML(c.id)}</strong><code>${escapeHTML(c.argv.join(" "))}</code><small>Folder: ${escapeHTML(c.cwd || ".")} · ${escapeHTML(c.timeout || 120)}s limit</small></li>`).join("") || '<li class="muted">None declared</li>';
  return `<details class="check-policy" data-plan-open="${escapeHTML(step.id + ":policy")}"><summary>${worker.length} worker check${worker.length === 1 ? "" : "s"} · ${local.length} local acceptance check${local.length === 1 ? "" : "s"}${inherited ? " · shared list" : " · separate lists"}</summary><div class="policy-columns"><div><strong>While coding · PC or notebook</strong><ul>${commands(worker)}</ul></div><div><strong>Before acceptance · this PC</strong><ul>${commands(local)}</ul></div></div>${!local.length ? '<p class="manual-notice">No local checks: explicit human review is required. Worker reports will remain unverified locally.</p>' : ""}</details>`;
}
$("edit-plan").onclick = () => { $("plan-json").value = JSON.stringify(planInfo.spec,null,2); dialog("plan-dialog"); };
$("plan-form").onsubmit = act(async e => {
  e.preventDefault(); let spec; try { spec = JSON.parse($("plan-json").value); } catch { throw new Error("The plan must be valid JSON."); }
  await post("/api/plan/edit",{id:selectedPlan,revision:planInfo.revision,spec}); $("plan-dialog").close(); await refreshPlans(); toast("Draft saved. Review commands and file scope before approval.");
});
$("generate-plan").onclick = act(async () => { await post("/api/plan/generate",{id:selectedPlan,revision:planInfo.revision}); await refresh(); toast("Drafting steps with your local model. The draft still needs your review."); });
$("approve-plan").onclick = act(async () => { await post("/api/plan/approve",{id:selectedPlan,revision:planInfo.revision}); await refreshPlans(); toast("Plan approved. Run or export a ready step."); });
$("stop-plan").onclick = act(async () => { await post("/api/plan/stop",{id:selectedPlan}); toast("Stopping acceptance checks. The accepted checkpoint stays in place."); });
$("plan-board").onclick = act(async e => {
  const button = e.target.closest("button"); if (!button) return;
  if (button.dataset.planRun || button.dataset.planCloud) {
    const result = await post("/api/plan/run",{id:selectedPlan,step_id:button.dataset.planRun || button.dataset.planCloud,notebook:Boolean(button.dataset.planCloud),allow_checks:$("plan-run-checks").checked});
    selectedJob = result.id; jobInfo = null; activeTab = "changes"; await refresh();
    if (result.download) window.location.assign(result.download);
    return;
  }
  if (button.dataset.planView) { selectedJob = button.dataset.planView; await refreshJob(true); $("session-section").scrollIntoView({behavior:"smooth",block:"start"}); }
  if (button.dataset.planAccept) { await post("/api/plan/accept",{job_id:button.dataset.planAccept,allow_checks:$("plan-checks").checked,manual:$("plan-manual").checked}); await refresh(); toast("Rechecking the review branch locally before advancing the checkpoint."); }
  if (button.dataset.planAbandon) { await post("/api/plan/abandon",{job_id:button.dataset.planAbandon}); await refresh(); toast("Attempt kept in history. A retry starts from the last accepted checkpoint."); }
  if (button.dataset.planAttach) { planUploadJob = button.dataset.planAttach; $("plan-result-file").click(); }
  if (button.dataset.planPolicy) {
    const step = planInfo.spec.steps.find(s => s.id === button.dataset.planPolicy);
    checkEditor = {id:selectedPlan,step_id:step.id,revision:planInfo.revision};
    $("step-checks-title").textContent = step.id + " · " + step.title;
    $("worker-checks-json").value = JSON.stringify(step.task.checks || [],null,2);
    $("inherit-checks").checked = !Object.hasOwn(step,"acceptance_checks");
    $("acceptance-checks-json").value = JSON.stringify(step.acceptance_checks ?? step.task.checks ?? [],null,2);
    $("acceptance-checks-json").disabled = $("inherit-checks").checked;
    dialog("step-checks-dialog");
  }
  if (button.dataset.planSetup) {
    button.disabled = true;
    try {
      const result = await api(`/api/plan/preflight?id=${encodeURIComponent(selectedPlan)}&step_id=${encodeURIComponent(button.dataset.planSetup)}`);
      showSetup(result);
    } finally { button.disabled = false; }
  }
});
$("inherit-checks").onchange = () => { $("acceptance-checks-json").disabled = $("inherit-checks").checked; };
$("step-checks-form").onsubmit = act(async e => {
  e.preventDefault(); let worker, acceptance;
  try { worker = JSON.parse($("worker-checks-json").value); acceptance = $("inherit-checks").checked ? null : JSON.parse($("acceptance-checks-json").value); }
  catch { throw new Error("Check lists must be valid JSON arrays."); }
  await post("/api/plan/checks",{...checkEditor,worker_checks:worker,acceptance_checks:acceptance});
  $("step-checks-dialog").close(); await refreshPlans(); toast("Check policy saved. Review both lists before approving.");
});
function showSetup(result) {
  $("preflight-title").textContent = result.status === "needs_setup" ? "Some tools need attention." : result.status === "no_checks" ? "Human review is required." : "Command tools found.";
  $("preflight-note").textContent = `Step ${result.step_id} · ${result.source === "review_candidate" ? "Review candidate" : "Accepted checkpoint"}\n${result.note}`;
  $("preflight-results").innerHTML = result.checks.map(c => `<article class="setup-row"><strong>${escapeHTML(c.id)}</strong><span class="status-pill ${c.status === "tools_found" ? "neutral" : "bad"}">${c.status === "tools_found" ? "Tool found" : "Needs setup"}</span><code>${escapeHTML(c.argv.join(" "))}</code><small>Folder: ${escapeHTML(c.cwd)}</small>${c.issues.map(issue => `<p>${escapeHTML(issue)}</p>`).join("")}${c.executable ? `<small>${escapeHTML(c.executable)}</small>` : ""}</article>`).join("") || '<p class="manual-notice">This step has no local acceptance commands. Accepting it needs explicit human review and does not verify worker check reports.</p>';
  $("preflight-message").textContent = result.message;
  dialog("preflight-dialog");
}
$("plan-result-file").onchange = act(async e => {
  const file = e.target.files[0]; if (!file) return;
  try {
    if (file.size > 25000000) throw new Error("Result ZIP exceeds the upload limit.");
    const uploaded = await api("/api/upload",{method:"POST",body:file,headers:{"Content-Type":"application/zip"}});
    await post("/api/plan/attach",{job_id:planUploadJob,upload_id:uploaded.upload_id}); selectedJob = planUploadJob; jobInfo = null; await refresh(); toast("Result attached to this step. Inspect its diff before accepting.");
  } finally { e.target.value = ""; }
});
$("review-plan").onclick = act(async () => {
  const result = await api("/api/plan/patch?id=" + selectedPlan);
  $("file-title").textContent = "COMBINED CHANGES · " + result.commit.slice(0,8);
  $("file-content").textContent = `Review branch: ${result.branch}\nWorktree: ${result.worktree}\n\n${result.diff || "No source changes."}`;
  dialog("file-dialog");
});
$("export-plan").onclick = () => {
  const blob = new Blob([JSON.stringify(planInfo,null,2)],{type:"application/json"});
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url; link.download = selectedPlan + ".json"; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
};
$("backup-checkpoint").onclick = act(async () => {
  const result = await post("/api/checkpoint/export",{id:selectedPlan});
  window.location.assign(result.download);
  toast("Checkpoint saved: latest accepted source and plan history. Unaccepted edits are excluded.");
});
$("restore-checkpoint").onclick = () => $("checkpoint-file").click();
$("checkpoint-file").onchange = act(async e => {
  const file = e.target.files[0]; if (!file) return;
  try {
    if (file.size > 25000000) throw new Error("Checkpoint ZIP exceeds the 25 MB upload limit.");
    checkpointUpload = await api("/api/checkpoint/upload",{method:"POST",body:file,headers:{"Content-Type":"application/zip"}});
    $("restore-summary").textContent = `${checkpointUpload.title}: ${checkpointUpload.files.length} source files, ${checkpointUpload.accepted_steps.length} previously accepted steps, ${checkpointUpload.remaining_steps.length} remaining steps. Goal: ${checkpointUpload.goal}`;
    $("restore-notice").textContent = checkpointUpload.notice;
    $("restore-destination").value = ""; dialog("restore-dialog");
  } finally { e.target.value = ""; }
});
$("restore-form").onsubmit = act(async e => {
  e.preventDefault(); const button = e.submitter; button.disabled = true;
  try {
    const result = await post("/api/checkpoint/restore",{upload_id:checkpointUpload.upload_id,destination:$("restore-destination").value});
    $("restore-dialog").close(); await refresh(); await selectProject(result.project.id);
    if (result.plan) { selectedPlan = result.plan.id; await refreshPlans(); }
    toast(result.plan ? "Accepted source restored. Review and approve the remaining draft before running it." : "Completed source restored. Create a new plan whenever you are ready.");
  } finally { button.disabled = false; }
});
$("checkpoint-history").onclick = () => {
  $("file-title").textContent = "IMPORTED HISTORY · CHECKS WERE NOT RERUN";
  $("file-content").textContent = JSON.stringify(projectInfo.recovery,null,2);
  dialog("file-dialog");
};

async function dismissWelcome() {
  const value = await post("/api/welcome", {welcome_dismissed:true});
  state.ui = value; $("welcome-dialog").close();
}
$("welcome-nav").onclick = () => dialog("welcome-dialog");
$("welcome-close").onclick = act(dismissWelcome);
$("welcome-later").onclick = act(dismissWelcome);
$("welcome-dialog").addEventListener("cancel", act(async event => {
  event.preventDefault(); await dismissWelcome();
}));
$("welcome-demo").onclick = act(async () => {
  $("welcome-demo").disabled = true;
  try {
    const existing = state.projects.find(p => p.demo);
    const p = existing || await post("/api/demo", {});
    await dismissWelcome(); await refresh(); await selectProject(p.id);
    toast("Demo selected. Click Run session when ready. Model decisions are simulated; edits and tests are real.");
  } finally { $("welcome-demo").disabled = false; }
});
$("welcome-local").onclick = act(async () => { await dismissWelcome(); $("settings-button").click(); });
$("welcome-cloud").onclick = act(async () => { await dismissWelcome(); dialog("cloud-dialog"); });
let setupReport = null;
async function inspectSetup() {
  setupReport = await api("/api/setup");
  $("setup-summary").textContent = setupReport.checks.map(c => `${c.id}: ${c.status}`).join(" · ") + ". Model and cloud runtime have not been checked.";
  return setupReport;
}
$("inspect-setup").onclick = act(inspectSetup);
$("download-setup").onclick = act(async () => {
  const report = await inspectSetup();
  const url = URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json"}));
  const link = document.createElement("a"); link.href = url; link.download = "aegaeon-setup.json";
  link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
refresh().then(() => {
  if (!state.ui?.welcome_dismissed && !document.querySelector("dialog[open]")) dialog("welcome-dialog");
  return checkConnection();
}).catch(error => toast(error.message,true));
setInterval(() => { if (!busy) refresh().catch(error => toast(error.message,true)); },2000);

let modelPoll = null, modelListingBusy = false, pendingModelDownload = null;
async function refreshModels() {
  if (modelListingBusy || !$("models-dialog").open) return;
  modelListingBusy = true;
  try {
    const value = await api("/api/models"), machine = value.hardware;
    const gb = n => n == null ? "unknown" : (n / 1024**3).toFixed(1) + " GiB";
    $("models-hardware").textContent = `RAM ${gb(machine.ram_bytes)} total, ${gb(machine.available_ram_bytes)} currently available. GPU: ${machine.gpus.map(g => g.name + " (" + g.free_mb + " MB free)").join(", ") || "not detected"}.`;
    $("models-notice").textContent = (value.runtime_found ? "llama-server found on PATH. " : "llama-server not found on PATH; a separate compatible runtime is required. ") + value.notice;
    const busy = ["downloading", "cancelling"].includes(value.download.status);
    $("models-catalog").innerHTML = value.models.map(m => `<section class="welcome-setup"><h3>${escapeHTML(m.name)}</h3><p>${m.parameters_b}B parameters · ${escapeHTML(m.quantization)} · ${(m.bytes/1e9).toFixed(2)} GB download</p><p>${escapeHTML(m.purpose)}. ${escapeHTML(m.capability)}.</p><p>Memory fit: ${escapeHTML(m.advice.fit)} · planning estimate ${gb(m.advice.estimated_runtime_bytes)}. ${escapeHTML(m.advice.execution)}</p><p class="hint">${escapeHTML(m.advice.notice)}</p><a href="${escapeHTML(m.source)}" target="_blank" rel="noreferrer">Official model and license</a><div class="plan-buttons"><button class="button" data-model-download="${m.id}" ${busy ? "disabled" : ""}>${pendingModelDownload === m.id ? "Confirm download " + (m.bytes/1e9).toFixed(2) + " GB" : m.downloaded ? "Download again" : "Download " + (m.bytes/1e9).toFixed(2) + " GB"}</button><button class="button" data-model-command="${m.id}" ${m.downloaded ? "" : "disabled"}>Verify and get launch command</button></div></section>`).join("");
    const progress = value.download;
    $("models-progress").textContent = `${progress.status}${progress.total ? " · " + Math.floor(100*(progress.received||0)/progress.total) + "%" : ""}${progress.error ? ": " + progress.error : ""}`;
    $("model-cancel").disabled = value.download.status !== "downloading";
  } finally { modelListingBusy = false; }
}
$("models-nav").onclick = act(async () => {
  pendingModelDownload = null; dialog("models-dialog"); await refreshModels();
  clearInterval(modelPoll); modelPoll = setInterval(() => refreshModels().catch(e => {$("models-error").textContent=e.message;}), 2500);
});
$("models-dialog").addEventListener("close", () => {clearInterval(modelPoll); pendingModelDownload=null;});
$("model-cancel").onclick = act(async () => {await post("/api/models/cancel", {}); await refreshModels();});
$("models-catalog").onclick = act(async event => {
  const download = event.target.closest("[data-model-download]"), command = event.target.closest("[data-model-command]");
  if (download) {
    if (pendingModelDownload !== download.dataset.modelDownload) {
      pendingModelDownload = download.dataset.modelDownload; await refreshModels(); return;
    }
    await post("/api/models/download", {id:pendingModelDownload, confirm_download:true});
    pendingModelDownload=null; await refreshModels();
  }
  if (command) {
    $("models-command").textContent = "Verifying downloaded bytes…";
    const result = await post("/api/models/command", {id:command.dataset.modelCommand});
    $("models-command").textContent = result.notice + "\n\n" + "& " + result.argv.map(x => "'" + x.replaceAll("'", "''") + "'").join(" ");
  }
});
