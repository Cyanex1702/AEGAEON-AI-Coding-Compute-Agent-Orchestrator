"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity as ActivityIcon,
  Box,
  Braces,
  ChevronRight,
  CircleStop,
  Clock3,
  Copy,
  Download,
  ExternalLink,
  NotebookTabs,
  FileCode2,
  Files,
  GitCommitHorizontal,
  Play,
  RotateCcw,
  ShieldCheck,
  TerminalSquare,
  X,
} from "lucide-react";
import { API_URL, api, projectExportUrl } from "@/lib/api";
import type { Activity, Job, Notebook, Project, ProjectFile, ProjectOrchestration, RemoteConnectivity, Run, Task, Worker } from "@/lib/types";
import { ArchitecturePanel } from "./architecture-panel";
import { RemoteConnectivityPanel } from "./remote-connectivity";
import { ActivityFeed, Badge, cn, exactTime, formatBytes, MiniBar, StatusDot, TaskIcon, timeAgo } from "./ui";

type Props = {
  project: Project;
  liveEvents: Activity[];
  onRefresh: () => Promise<void>;
  workers: Worker[];
};

export function ProjectView({ project, liveEvents, onRefresh, workers }: Props) {
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(
    project.tasks[0]?.id ?? null,
  );
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [history, setHistory] = useState<Activity[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [architecture, setArchitecture] = useState<ProjectOrchestration | null>(null);
  const [tab, setTab] = useState<"workflow" | "architecture" | "files">("workflow");
  const [actionError, setActionError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [remoteStatus, setRemoteStatus] = useState<RemoteConnectivity | null>(null);
  const [generatingWorkers, setGeneratingWorkers] = useState(false);
  const active = ["planning", "running", "testing", "reviewing"].includes(project.status);
  const brokeMode = project.options.execution_mode === "broke_boy";
  const workerSetupVisible =
    brokeMode &&
    ["draft", "failed", "cancelled"].includes(project.status);
  const selectedModel = String(project.options.selected_model ?? "");
  const remoteTarget = project.options.compute_target !== "local_gpu";
  const remoteReady = !remoteTarget || remoteStatus?.state === "ready";
  const readyModelWorkers = workers.filter(
    (worker) =>
      worker.status !== "offline" &&
      worker.capabilities.includes("model.generate") &&
      worker.models.some((model) => model.id === selectedModel && model.loaded),
  );

  const selectedTask = useMemo(
    () =>
      project.tasks.find((item) => item.id === selectedTaskId) ?? project.tasks[0] ?? null,
    [project.tasks, selectedTaskId],
  );

  useEffect(() => {
    let cancelled = false;
    let refreshing = false;
    async function refreshDetail() {
      if (refreshing) return;
      refreshing = true;
      try {
        const [fileData, events, jobData, runData, architectureData] = await Promise.all([
          api.files(project.id),
          api.events(project.id),
          api.jobs(project.id),
          api.runs(project.id),
          api.orchestration(project.id),
        ]);
        if (!cancelled) {
          setFiles(fileData);
          setHistory(events);
          setJobs(jobData);
          setRuns(runData);
          setArchitecture(architectureData);
          setDetailError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setDetailError(
            reason instanceof Error ? reason.message : "Project details are temporarily unavailable",
          );
        }
      } finally {
        refreshing = false;
      }
    }
    void refreshDetail();
    const timer = active ? window.setInterval(() => void refreshDetail(), 2000) : undefined;
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [active, detailRefresh, project.id, project.updated_at]);

  const events = useMemo(() => {
    const combined = [...liveEvents.filter((item) => item.project_id === project.id), ...history];
    return Array.from(new Map(combined.map((item) => [item.id, item])).values()).sort(
      (a, b) => +new Date(b.created_at) - +new Date(a.created_at),
    );
  }, [history, liveEvents, project.id]);

  async function run() {
    setActionError(null);
    try {
      await api.runProject(project.id);
      await onRefresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not start project");
    }
  }

  async function generateWorkers() {
    setGeneratingWorkers(true);
    setActionError(null);
    try {
      setNotebooks(await api.generateNotebooks(project.id, {}));
      await onRefresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not generate notebooks");
    } finally {
      setGeneratingWorkers(false);
    }
  }

  async function cancel() {
    setActionError(null);
    try {
      await api.cancelProject(project.id);
      await onRefresh();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : "Could not cancel project");
    }
  }

  return (
    <div className="page-wrap project-page">
      <div className="project-heading">
        <div className="min-w-0">
          <div className="mb-3 flex items-center gap-2 text-[11px] text-zinc-600"><span>Projects</span><ChevronRight className="h-3 w-3" /><span className="truncate text-zinc-400">{project.name}</span></div>
          <div className="flex flex-wrap items-center gap-3"><h1 className="truncate">{project.name}</h1><Badge tone={project.status === "completed" ? "acid" : project.status === "failed" ? "danger" : "cyan"}><StatusDot status={project.status} pulse={active} /> {project.status}</Badge></div>
          <p className="mt-2 max-w-3xl truncate">{project.summary || project.prompt}</p>
          <div className="mt-2 font-mono text-[10px] text-zinc-600">{project.active_run_id ? `Active run ${project.active_run_id}` : "No active run"} · {runs.length} historical {runs.length === 1 ? "run" : "runs"}</div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button className="secondary-button" onClick={() => navigator.clipboard.writeText(project.workspace_path)} title="Copy canonical workspace path"><Copy className="h-4 w-4" /> <span className="hidden sm:inline">Copy path</span></button>
          <a className="secondary-button" href={projectExportUrl(project.id)}><Download className="h-4 w-4" /> <span className="hidden sm:inline">Export ZIP</span></a>
          {active ? <button className="danger-button" onClick={cancel}><CircleStop className="h-4 w-4" /> Stop</button> : <button className="primary-button" onClick={run} title={brokeMode && readyModelWorkers.length === 0 ? "No compatible worker is registered; click to see the exact requirement" : undefined}><Play className="h-4 w-4" /> {project.status === "draft" ? "Build" : "Run again"}</button>}
        </div>
      </div>
      {actionError && <div className="error-banner"><X className="h-4 w-4" />{actionError}</div>}
      {detailError && <div className="error-banner"><X className="h-4 w-4" /><span>{detailError}. Existing project data remains visible.</span><button className="ml-auto" onClick={() => setDetailRefresh((value) => value + 1)}><RotateCcw className="h-3.5 w-3.5" /> Retry</button></div>}

      {workerSetupVisible && (
        <section className="panel mb-5 overflow-hidden border-cyan-300/10">
          <div className="panel-header"><div><div className="eyebrow">Broke Boy launchpad</div><h3>Connect your model worker</h3></div><Badge tone={readyModelWorkers.length ? "acid" : "warning"}>{readyModelWorkers.length ? `${readyModelWorkers.length} ready` : "setup required"}</Badge></div>
          <div className="grid gap-5 p-5 lg:grid-cols-[1fr_1.2fr]">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-zinc-600">Selected model</div><div className="mt-2 break-all font-mono text-xs text-cyan-200">{selectedModel}</div>
              <div className="mt-5 rounded-lg border border-white/[.06] bg-black/20 p-4">
                <div className="mb-3 text-[10px] uppercase tracking-widest text-zinc-600">Remote connection</div>
                <RemoteConnectivityPanel onStatus={setRemoteStatus} />
              </div>
              <button className="secondary-button mt-4" onClick={() => void generateWorkers()} disabled={generatingWorkers || !remoteReady} title={!remoteReady ? "Set up and verify the remote connection first" : undefined}><NotebookTabs className="h-4 w-4" />{generatingWorkers ? "Generating…" : `Generate ${Number(project.options.worker_count ?? 1)} notebook(s)`}</button>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-zinc-600">Run-All notebooks</div>
              {notebooks.length ? <div className="mt-2 space-y-2">{notebooks.map((notebook) => <div key={notebook.artifact_id} className="rounded-md border border-white/[.06] bg-black/20 p-3"><div className="flex items-center justify-between gap-3"><div className="min-w-0"><div className="truncate text-xs text-zinc-200">{notebook.filename}</div><div className="mt-1 font-mono text-[10px] text-acid">Pairing code: {notebook.pairing_code}</div></div><a className="icon-button" href={`${API_URL}${notebook.download_url}`} title="Download notebook"><ExternalLink className="h-4 w-4" /></a></div></div>)}</div> : <div className="mt-2 rounded-md border border-dashed border-white/[.08] p-5 text-xs leading-5 text-zinc-600">Generate the notebooks, download each one, choose a GPU runtime in Colab, run all cells, and enter its one-time pairing code.</div>}
              <div className="mt-3 text-[10px] text-amber-200/80">Tunnel verification confirms the route only. A worker is ready after it registers the exact loaded model. Build remains clickable and will report any missing requirement.</div>
            </div>
          </div>
        </section>
      )}

      <section className="project-summary-grid">
        <div className="summary-card col-span-2">
          <div className="flex items-end justify-between"><div><div className="eyebrow">Overall progress</div><div className="mt-2 text-2xl font-medium tabular-nums text-zinc-100">{project.progress}%</div></div><div className="text-right"><div className="text-xs text-zinc-500">{project.tasks.filter((task) => task.status === "COMPLETED").length} of {project.tasks.length || 0} tasks</div><div className="mt-1 text-[10px] uppercase tracking-widest text-zinc-700">Canonical state</div></div></div>
          <div className="mt-4"><MiniBar value={project.progress} /></div>
        </div>
        <div className="summary-card"><div className="eyebrow">Execution strategy</div><div className="mt-3 flex items-center gap-3"><div className="summary-icon"><GitCommitHorizontal className="h-4 w-4" /></div><div><div className="text-sm font-medium capitalize text-zinc-200">{project.strategy.replaceAll("_", " ")}</div><div className="mt-1 text-[11px] text-zinc-600">Auto-routed · extensible</div></div></div></div>
        <div className="summary-card"><div className="eyebrow">Verification</div><div className="mt-3 flex items-center gap-3"><div className="summary-icon"><ShieldCheck className="h-4 w-4" /></div><div><div className="text-sm font-medium text-zinc-200">{project.status === "completed" ? "Tests passing" : active ? "Pending completion" : "Not verified"}</div><div className="mt-1 text-[11px] text-zinc-600">pytest · review loop</div></div></div></div>
      </section>

      <div className="project-tabs"><button className={cn(tab === "workflow" && "project-tab-active")} onClick={() => setTab("workflow")}><ActivityIcon className="h-3.5 w-3.5" /> Workflow</button><button className={cn(tab === "architecture" && "project-tab-active")} onClick={() => setTab("architecture")}><Braces className="h-3.5 w-3.5" /> Architecture <span>{architecture?.milestones.length ?? 0}</span></button><button className={cn(tab === "files" && "project-tab-active")} onClick={() => setTab("files")}><Files className="h-3.5 w-3.5" /> Files <span>{files.length}</span></button></div>

      {tab === "workflow" ? (
        <section className="project-workspace">
          <div className="panel task-pipeline-panel">
            <div className="panel-header"><div><div className="eyebrow">Task DAG</div><h3>Execution pipeline</h3></div><Badge>{project.tasks.length} nodes</Badge></div>
            <div className="task-pipeline">
              {project.tasks.length ? project.tasks.map((task, index) => (
                <button key={task.id} className={cn("task-node", selectedTask?.id === task.id && "task-node-active")} onClick={() => setSelectedTaskId(task.id)}>
                  {index < project.tasks.length - 1 && <span className={cn("task-connector", task.status === "COMPLETED" && "task-connector-complete")} />}
                  <span className={cn("task-state-icon", task.status === "COMPLETED" && "task-state-complete", ["RUNNING", "ASSIGNED", "QUEUED", "WAITING_FOR_WORKER"].includes(task.status) && "task-state-running")}><TaskIcon status={task.status} /></span>
                  <span className="min-w-0 flex-1 text-left"><span className="block truncate text-[13px] font-medium text-zinc-200">{task.title}</span><span className="mt-1 block text-[10px] uppercase tracking-[.14em] text-zinc-600">{task.agent_role} · {taskStatusLabel(task.status)}</span></span>
                  {task.retry_count > 0 && <Badge tone="warning"><RotateCcw className="h-3 w-3" /> {task.retry_count}</Badge>}
                  {task.duration_seconds != null && <span className="text-[10px] tabular-nums text-zinc-600">{task.duration_seconds.toFixed(1)}s</span>}
                </button>
              )) : <div className="empty-state py-14">Build the project to create its task DAG.</div>}
            </div>
          </div>

          <div className="panel task-detail-panel">
            {selectedTask ? <TaskDetail task={selectedTask} jobs={jobs.filter((job) => job.task_id === selectedTask.id)} /> : <div className="empty-state flex h-full items-center justify-center">Select a task to inspect it.</div>}
          </div>

          <div className="panel activity-panel">
            <div className="panel-header"><div><div className="eyebrow">Telemetry</div><h3>Activity stream</h3></div><StatusDot status={active ? "running" : "online"} pulse={active} /></div>
            <ActivityFeed events={events.slice(0, 12)} empty="Run the project to stream orchestration events." />
          </div>
        </section>
      ) : tab === "files" ? (
        <section className="panel file-panel">
          <div className="panel-header"><div><div className="eyebrow">Canonical repository</div><h3>{project.workspace_path}</h3></div><Badge>{files.length} files</Badge></div>
          <div className="file-list">
            {files.length ? files.map((file) => <div className="file-row" key={file.path}><FileCode2 className="h-4 w-4 text-zinc-600" /><span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-300">{file.path}</span><span className="text-[10px] tabular-nums text-zinc-700">{formatBytes(file.size)}</span></div>) : <div className="empty-state py-16">The canonical repository contains no generated files yet.</div>}
          </div>
        </section>
      ) : (
        <ArchitecturePanel
          key={
            String(architecture?.compute_plan?.strategy ?? "loading") +
            String(architecture?.compute_plan?.selected_worker_count ?? 0)
          }
          projectId={project.id}
          data={architecture}
          onChanged={setArchitecture}
        />
      )}
    </div>
  );
}

function TaskDetail({ task, jobs }: { task: Task; jobs: Job[] }) {
  const attempts = [...jobs].sort((a, b) => b.attempt - a.attempt);
  return (
    <div className="task-detail">
      <div className="task-detail-head"><div className="summary-icon"><Braces className="h-4 w-4" /></div><div className="min-w-0 flex-1"><div className="eyebrow">{task.agent_role} agent</div><h3 className="mt-1 truncate">{task.title}</h3></div><Badge tone={task.status === "COMPLETED" ? "acid" : task.status === "FAILED" ? "danger" : "cyan"}>{taskStatusLabel(task.status)}</Badge></div>
      <p className="task-description">{task.description}</p>
      <div className="detail-grid"><Detail icon={<Box />} label="Worker" value={task.worker_id ?? (task.status === "WAITING_FOR_WORKER" ? "Queued for compatible capacity" : "Waiting for assignment")} /><Detail icon={<Clock3 />} label="Duration" value={task.duration_seconds == null ? "—" : `${task.duration_seconds.toFixed(2)} seconds`} /><Detail icon={<RotateCcw />} label="Attempts" value={`${task.retry_count + 1} / ${task.max_retries + 1}`} /><Detail icon={<GitCommitHorizontal />} label="Job" value={task.job_id ?? "Not dispatched"} /></div>
      <div className="detail-section">
        <div className="mb-3 flex items-center justify-between"><div className="eyebrow">Dispatch history</div><Badge>{attempts.length} {attempts.length === 1 ? "lease" : "leases"}</Badge></div>
        {attempts.length ? <div className="space-y-2">{attempts.map((job) => (
          <div key={job.id} className="rounded-md border border-white/[.06] bg-black/20 px-3 py-2.5">
            <div className="flex items-center gap-2"><span className="font-mono text-[10px] text-zinc-500">#{job.attempt + 1}</span><Badge tone={job.status === "COMPLETED" ? "acid" : job.status === "FAILED" || job.status === "INTERRUPTED" ? "danger" : "cyan"}>{job.status}</Badge><span className="ml-auto truncate text-[10px] text-zinc-600" title={job.worker_id ?? "Unassigned"}>{job.worker_id ?? "Unassigned"}</span></div>
            <div className="mt-2 flex items-center justify-between gap-3 text-[10px] text-zinc-700"><span title={exactTime(job.completed_at ?? job.started_at ?? job.assigned_at)}>{job.completed_at ? `Finished ${timeAgo(job.completed_at)}` : job.started_at ? `Started ${timeAgo(job.started_at)}` : job.assigned_at ? `Assigned ${timeAgo(job.assigned_at)}` : "Queued"}</span><span className="truncate font-mono" title={job.id}>{job.id}</span></div>
            {job.error && <div className="mt-2 flex items-start gap-2 rounded border border-red-400/10 bg-red-950/10 p-2 text-[10px] leading-relaxed text-red-400/80"><span className="min-w-0 flex-1">{job.error}</span><button className="diagnostic-inline-copy" onClick={() => void navigator.clipboard.writeText(jobErrorBlock(job))}><Copy className="h-3 w-3" />Copy Error</button></div>}
            {Object.keys(job.diagnostics).length > 0 && <details className="mt-2 rounded border border-red-400/10 bg-red-950/10 p-2 text-[10px] text-zinc-500"><summary className="cursor-pointer text-red-300/80">Structured failure diagnostics</summary><div className="mt-2 grid grid-cols-2 gap-2"><span>Stage: {String(job.failure_stage ?? job.diagnostics.stage ?? "unknown")}</span><span>Class: {String(job.failure_classification ?? job.diagnostics.classification ?? "worker_failure")}</span><span>Error: {String(job.error_type ?? job.diagnostics.error_type ?? "Error")}</span><span>Device: {String(job.diagnostics.device ?? "unknown")}</span></div><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-[9px] leading-relaxed text-zinc-600">{String(job.diagnostics.traceback ?? job.diagnostics.error_repr ?? "No traceback reported")}</pre></details>}
            {job.logs.length > 0 && <div className="mt-2 space-y-1 border-t border-white/[.04] pt-2">{job.logs.slice(-8).map((entry, index) => <div key={`${job.id}-log-${index}`} className="flex gap-2 text-[10px]"><span className="w-16 shrink-0 font-mono text-cyan-500/70">{entry.stage ?? "worker"}</span><span className="text-zinc-500">{entry.message ?? "Progress update"}</span></div>)}</div>}
          </div>
        ))}</div> : <div className="muted-line">No worker lease has been created for this task.</div>}
      </div>
      <div className="detail-section"><div className="eyebrow mb-3">Files modified</div>{task.files_modified.length ? <div className="tag-list">{task.files_modified.map((file) => <span key={file}><FileCode2 className="h-3 w-3" />{file}</span>)}</div> : <div className="muted-line">No file artifacts for this task.</div>}</div>
      <div className="detail-section min-h-0"><div className="mb-3 flex items-center justify-between"><div className="eyebrow">Agent logs</div><TerminalSquare className="h-3.5 w-3.5 text-zinc-700" /></div><div className="log-console">{task.logs.length ? task.logs.map((log, index) => <div key={`${index}-${log}`}><span>{String(index + 1).padStart(2, "0")}</span>{log}</div>) : <p>Awaiting execution output…</p>}</div></div>
    </div>
  );
}

function Detail({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div><div className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-zinc-700">{icon}{label}</div><div className="mt-1.5 truncate text-xs text-zinc-300" title={value}>{value}</div></div>;
}


function jobErrorBlock(job: Job) {
  return [
    `Job: ${job.id}`,
    `Task: ${job.task_id}`,
    `Worker: ${job.worker_id ?? "—"}`,
    `Stage: ${job.failure_stage ?? job.diagnostics.stage ?? "—"}`,
    `Classification: ${job.failure_classification ?? job.diagnostics.classification ?? "—"}`,
    `Error Type: ${job.error_type ?? job.diagnostics.error_type ?? "—"}`,
    `Message: ${job.error ?? "—"}`,
    `Retry Strategy: ${job.retry_strategy ?? "—"}`,
    `Timestamp: ${job.failed_at ?? job.completed_at ?? job.updated_at}`,
  ].join("\n");
}

function taskStatusLabel(status: string) {
  const labels: Record<string, string> = {
    QUEUED: "Queued",
    WAITING_FOR_WORKER: "Waiting for worker",
    ASSIGNED: "Assigned",
    RUNNING: "Running",
    RETRYING: "Retrying",
    COMPLETED: "Completed",
    FAILED: "Failed",
    BLOCKED: "Blocked",
    READY: "Ready",
    PENDING: "Pending",
  };
  return labels[status] ?? status.replaceAll("_", " ").toLowerCase();
}



