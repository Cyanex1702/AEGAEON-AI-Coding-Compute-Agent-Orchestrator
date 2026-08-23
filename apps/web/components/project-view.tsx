"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity as ActivityIcon,
  Box,
  Braces,
  Check,
  ChevronRight,
  CircleStop,
  Clock3,
  Code2,
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
import type { Activity, Job, Notebook, Project, ProjectFile, RemoteConnectivity, Task, Worker } from "@/lib/types";
import { RemoteConnectivityPanel } from "./remote-connectivity";
import { ActivityFeed, Badge, cn, formatBytes, MiniBar, StatusDot, TaskIcon, timeAgo } from "./ui";

type Props = {
  project: Project;
  liveEvents: Activity[];
  onRefresh: () => Promise<void>;
};

export function ProjectView({ project, liveEvents, onRefresh }: Props) {
  const [selectedTask, setSelectedTask] = useState<Task | null>(project.tasks[0] ?? null);
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [history, setHistory] = useState<Activity[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tab, setTab] = useState<"workflow" | "files">("workflow");
  const [actionError, setActionError] = useState<string | null>(null);
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [remoteStatus, setRemoteStatus] = useState<RemoteConnectivity | null>(null);
  const [generatingWorkers, setGeneratingWorkers] = useState(false);
  const active = ["planning", "running", "testing", "reviewing"].includes(project.status);
  const brokeMode = project.options.execution_mode === "broke_boy";
  const selectedModel = String(project.options.selected_model ?? "");
  const remoteTarget = project.options.compute_target !== "local_gpu";
  const remoteReady = !remoteTarget || remoteStatus?.state === "ready";
  const readyModelWorkers = workers.filter(
    (worker) =>
      worker.status !== "offline" &&
      worker.capabilities.includes("model.generate") &&
      worker.models.some((model) => model.id === selectedModel && model.loaded),
  );

  useEffect(() => {
    const current = project.tasks.find((item) => item.id === selectedTask?.id);
    if (current) setSelectedTask(current);
    else if (project.tasks.length) setSelectedTask(project.tasks[0]);
  }, [project.tasks, selectedTask?.id]);

  useEffect(() => {
    void Promise.all([api.files(project.id), api.events(project.id), api.jobs(project.id), api.workers()]).then(([fileData, events, jobData, workerData]) => {
      setFiles(fileData);
      setHistory(events);
      setJobs(jobData);
      setWorkers(workerData);
    });
  }, [project.id, project.updated_at]);

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
      setWorkers(await api.workers());
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
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button className="secondary-button" onClick={() => navigator.clipboard.writeText(project.workspace_path)} title="Copy canonical workspace path"><Copy className="h-4 w-4" /> <span className="hidden sm:inline">Copy path</span></button>
          <a className="secondary-button" href={projectExportUrl(project.id)}><Download className="h-4 w-4" /> <span className="hidden sm:inline">Export ZIP</span></a>
          {active ? <button className="danger-button" onClick={cancel}><CircleStop className="h-4 w-4" /> Stop</button> : <button className="primary-button" onClick={run} disabled={brokeMode && readyModelWorkers.length === 0} title={brokeMode && readyModelWorkers.length === 0 ? "Connect a compatible model worker first" : undefined}><Play className="h-4 w-4" /> {project.status === "draft" ? "Build" : "Run again"}</button>}
        </div>
      </div>
      {actionError && <div className="error-banner"><X className="h-4 w-4" />{actionError}</div>}

      {brokeMode && project.status === "draft" && (
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
              <div className="mt-3 text-[10px] text-zinc-600">Build unlocks when a worker reports the exact model as loaded and ready.</div>
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

      <div className="project-tabs"><button className={cn(tab === "workflow" && "project-tab-active")} onClick={() => setTab("workflow")}><ActivityIcon className="h-3.5 w-3.5" /> Workflow</button><button className={cn(tab === "files" && "project-tab-active")} onClick={() => setTab("files")}><Files className="h-3.5 w-3.5" /> Files <span>{files.length}</span></button></div>

      {tab === "workflow" ? (
        <section className="project-workspace">
          <div className="panel task-pipeline-panel">
            <div className="panel-header"><div><div className="eyebrow">Task DAG</div><h3>Execution pipeline</h3></div><Badge>{project.tasks.length} nodes</Badge></div>
            <div className="task-pipeline">
              {project.tasks.length ? project.tasks.map((task, index) => (
                <button key={task.id} className={cn("task-node", selectedTask?.id === task.id && "task-node-active")} onClick={() => setSelectedTask(task)}>
                  {index < project.tasks.length - 1 && <span className={cn("task-connector", task.status === "COMPLETED" && "task-connector-complete")} />}
                  <span className={cn("task-state-icon", task.status === "COMPLETED" && "task-state-complete", ["RUNNING", "ASSIGNED"].includes(task.status) && "task-state-running")}><TaskIcon status={task.status} /></span>
                  <span className="min-w-0 flex-1 text-left"><span className="block truncate text-[13px] font-medium text-zinc-200">{task.title}</span><span className="mt-1 block text-[10px] uppercase tracking-[.14em] text-zinc-600">{task.agent_role} · {task.status}</span></span>
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
      ) : (
        <section className="panel file-panel">
          <div className="panel-header"><div><div className="eyebrow">Canonical repository</div><h3>{project.workspace_path}</h3></div><Badge>{files.length} files</Badge></div>
          <div className="file-list">
            {files.length ? files.map((file) => <div className="file-row" key={file.path}><FileCode2 className="h-4 w-4 text-zinc-600" /><span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-300">{file.path}</span><span className="text-[10px] tabular-nums text-zinc-700">{formatBytes(file.size)}</span></div>) : <div className="empty-state py-16">The canonical repository contains no generated files yet.</div>}
          </div>
        </section>
      )}
    </div>
  );
}

function TaskDetail({ task, jobs }: { task: Task; jobs: Job[] }) {
  const attempts = [...jobs].sort((a, b) => b.attempt - a.attempt);
  return (
    <div className="task-detail">
      <div className="task-detail-head"><div className="summary-icon"><Braces className="h-4 w-4" /></div><div className="min-w-0 flex-1"><div className="eyebrow">{task.agent_role} agent</div><h3 className="mt-1 truncate">{task.title}</h3></div><Badge tone={task.status === "COMPLETED" ? "acid" : task.status === "FAILED" ? "danger" : "cyan"}>{task.status}</Badge></div>
      <p className="task-description">{task.description}</p>
      <div className="detail-grid"><Detail icon={<Box />} label="Worker" value={task.worker_id ?? "Waiting for assignment"} /><Detail icon={<Clock3 />} label="Duration" value={task.duration_seconds == null ? "—" : `${task.duration_seconds.toFixed(2)} seconds`} /><Detail icon={<RotateCcw />} label="Attempts" value={`${task.retry_count + 1} / ${task.max_retries + 1}`} /><Detail icon={<GitCommitHorizontal />} label="Job" value={task.job_id ?? "Not dispatched"} /></div>
      <div className="detail-section">
        <div className="mb-3 flex items-center justify-between"><div className="eyebrow">Dispatch history</div><Badge>{attempts.length} {attempts.length === 1 ? "lease" : "leases"}</Badge></div>
        {attempts.length ? <div className="space-y-2">{attempts.map((job) => (
          <div key={job.id} className="rounded-md border border-white/[.06] bg-black/20 px-3 py-2.5">
            <div className="flex items-center gap-2"><span className="font-mono text-[10px] text-zinc-500">#{job.attempt + 1}</span><Badge tone={job.status === "COMPLETED" ? "acid" : job.status === "FAILED" || job.status === "INTERRUPTED" ? "danger" : "cyan"}>{job.status}</Badge><span className="ml-auto truncate text-[10px] text-zinc-600" title={job.worker_id ?? "Unassigned"}>{job.worker_id ?? "Unassigned"}</span></div>
            <div className="mt-2 flex items-center justify-between gap-3 text-[10px] text-zinc-700"><span>{job.completed_at ? `Finished ${timeAgo(job.completed_at)}` : job.started_at ? `Started ${timeAgo(job.started_at)}` : job.assigned_at ? `Assigned ${timeAgo(job.assigned_at)}` : "Queued"}</span><span className="truncate font-mono" title={job.id}>{job.id}</span></div>
            {job.error && <div className="mt-2 text-[10px] leading-relaxed text-red-400/80">{job.error}</div>}
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

