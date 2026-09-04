"use client";

import { useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpRight,
  Boxes,
  CheckCircle2,
  Cpu,
  FolderKanban,
  Gauge,
  Pin,
  PinOff,
  Plus,
  Radio,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Activity, DashboardStats, Project, Worker } from "@/lib/types";
import { ActivityFeed, Badge, exactTime, MiniBar, StatusDot, timeAgo } from "./ui";

type Props = {
  stats: DashboardStats;
  projects: Project[];
  workers: Worker[];
  events: Activity[];
  onProject: (id: string) => void;
  onCreate: () => void;
  onRefresh: () => Promise<void>;
};

export function Dashboard({ stats, projects, workers, events, onProject, onCreate, onRefresh }: Props) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [managementError, setManagementError] = useState<string | null>(null);
  const onlineWorkers = workers.filter((item) => item.status !== "offline");
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return projects.filter((project) => {
      const matchesQuery = !normalized || project.name.toLowerCase().includes(normalized) || project.prompt.toLowerCase().includes(normalized);
      const matchesStatus = status === "all" || project.status === status || (status === "active" && ["planning", "running", "testing", "reviewing"].includes(project.status));
      return matchesQuery && matchesStatus;
    });
  }, [projects, query, status]);

  async function manage(project: Project, action: "pin" | "up" | "down" | "delete") {
    if (action === "delete") {
      const confirmed = window.confirm(
        `Delete "${project.name}"?\n\nThis permanently removes its runs, tasks, jobs, artifacts, architecture records, and repository.`,
      );
      if (!confirmed) return;
    }
    setBusyId(project.id);
    setManagementError(null);
    try {
      if (action === "pin") await api.pinProject(project.id, !project.is_pinned);
      if (action === "up" || action === "down") await api.moveProject(project.id, action);
      if (action === "delete") await api.deleteProject(project.id);
      await onRefresh();
    } catch (reason) {
      setManagementError(reason instanceof Error ? reason.message : "Project action failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="page-wrap">
      <div className="page-heading">
        <div><div className="eyebrow mb-2">Command center</div><h1>Good evening, operator.</h1><p>Plan, dispatch, and verify autonomous software builds from one local control plane.</p></div>
        <button className="primary-button" onClick={onCreate}><Plus className="h-4 w-4" /> Start a build</button>
      </div>

      <section className="metric-grid">
        <Metric icon={<Radio />} label="Active builds" value={stats.active_projects} note={`${stats.running_tasks} tasks in flight`} />
        <Metric icon={<Cpu />} label="Online compute" value={stats.online_workers} note={`${stats.gpu_workers} GPU workers`} />
        <Metric icon={<FolderKanban />} label="Total projects" value={stats.projects} note="Canonical repositories" />
        <Metric icon={<CheckCircle2 />} label="System state" value="READY" note="Controller responding" accent />
      </section>

      {projects.length === 0 && (
        <section className="launch-card">
          <div className="launch-orbit orbit-one" /><div className="launch-orbit orbit-two" />
          <div className="relative z-10 max-w-xl"><Badge tone="acid"><Sparkles className="mr-1.5 h-3 w-3" /> Demo mode online</Badge><h2>Your compute fabric is waiting.</h2><p>Start with the calculator API demo. AEGAEON will plan the DAG, dispatch code jobs, apply Git patches, run pytest, and review the final repository.</p><button className="primary-button mt-6" onClick={onCreate}>Create first project <ArrowUpRight className="h-4 w-4" /></button></div>
          <div className="launch-node"><Boxes className="h-6 w-6" /><span>PLANNER</span></div>
        </section>
      )}

      <section className="dashboard-grid">
        <div className="panel overflow-hidden lg:col-span-2">
          <div className="panel-header project-management-header">
            <div><div className="eyebrow">Build queue</div><h3>Projects</h3></div>
            <div className="project-list-tools">
              <label><Search className="h-3.5 w-3.5" /><input aria-label="Search projects" placeholder="Search projects" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
              <select aria-label="Filter project status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All statuses</option><option value="active">Active</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="draft">Draft</option></select>
            </div>
          </div>
          {managementError && <div className="project-management-error">{managementError}</div>}
          <div className="project-table project-table-managed">
            {visible.length ? visible.map((project, index) => {
              const activeWorkerCount = new Set(project.tasks.filter((task) => task.worker_id && ["RUNNING", "ASSIGNED"].includes(task.status)).map((task) => task.worker_id)).size;
              return (
                <div className="project-table-row project-managed-row" key={project.id}>
                  <button className="project-open-button" onClick={() => onProject(project.id)}>
                    <div className="project-avatar">{project.name.slice(0, 2).toUpperCase()}</div>
                    <div className="min-w-0 flex-1 text-left">
                      <div className="flex items-center gap-2"><span className="truncate text-sm font-medium text-zinc-100">{project.name}</span>{project.is_pinned && <Pin className="h-3 w-3 shrink-0 text-acid" />}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-zinc-600"><span>{project.tasks.filter((task) => task.status === "COMPLETED").length}/{project.tasks.length || 0} tasks</span><span>·</span><time title={exactTime(project.updated_at)} dateTime={project.updated_at}>{timeAgo(project.updated_at)}</time><span className="text-zinc-700">{exactTime(project.updated_at)}</span>{activeWorkerCount > 0 && <><span>·</span><span>{activeWorkerCount} active worker{activeWorkerCount === 1 ? "" : "s"}</span></>}</div>
                    </div>
                    <div className="hidden w-28 sm:block"><MiniBar value={project.progress} /><span className="mt-1.5 block text-right text-[10px] text-zinc-600">{project.progress}%</span></div>
                    <Badge tone={project.status === "completed" ? "acid" : project.status === "failed" ? "danger" : "cyan"}><StatusDot status={project.status} /> {project.status}</Badge>
                  </button>
                  <div className="project-actions" aria-label={`Manage ${project.name}`}>
                    <button disabled={busyId === project.id} onClick={() => void manage(project, "pin")} title={project.is_pinned ? "Unpin project" : "Pin project"}>{project.is_pinned ? <PinOff /> : <Pin />}</button>
                    <button disabled={busyId === project.id || index === 0} onClick={() => void manage(project, "up")} title="Move up"><ArrowUp /></button>
                    <button disabled={busyId === project.id || index === visible.length - 1} onClick={() => void manage(project, "down")} title="Move down"><ArrowDown /></button>
                    <button className="project-delete-action" disabled={busyId === project.id} onClick={() => void manage(project, "delete")} title="Delete project"><Trash2 /></button>
                  </div>
                </div>
              );
            }) : <div className="empty-state py-16">No projects match this view.</div>}
          </div>
        </div>

        <div className="panel"><div className="panel-header"><div><div className="eyebrow">Event stream</div><h3>Live activity</h3></div><StatusDot status="online" pulse /></div><ActivityFeed events={events.slice(0, 7)} empty="Events appear here while a project runs." /></div>

        <div className="panel lg:col-span-3"><div className="panel-header"><div><div className="eyebrow">Compute mesh</div><h3>Workers</h3></div><Badge>{onlineWorkers.length} connected</Badge></div><div className="worker-strip">{onlineWorkers.length ? onlineWorkers.map((worker) => <div className="worker-chip" key={worker.id}><div className="worker-icon"><Cpu className="h-4 w-4" /></div><div className="min-w-0 flex-1"><div className="flex items-center gap-2 text-sm text-zinc-200"><span className="truncate">{worker.hostname}</span><StatusDot status={worker.status} pulse={worker.status === "busy"} /></div><div className="mt-1 text-[10px] uppercase tracking-wider text-zinc-600">{worker.hardware.gpu?.available ? worker.hardware.gpu.name : `${worker.hardware.cpu_cores ?? 1} CPU cores`}</div></div>{worker.progress_percent > 0 ? <div className="w-20"><MiniBar value={worker.progress_percent} tone="cyan" /><span className="mt-1 block text-right text-[10px] text-zinc-600">{worker.progress_percent.toFixed(0)}%</span></div> : worker.status === "busy" ? <span className="text-[10px] text-cyan-400">{worker.stage?.replaceAll("_", " ") ?? "Working…"}</span> : null}</div>) : <div className="worker-empty"><Gauge className="h-5 w-5" /><span>No external workers. Local demo runtime will execute builds.</span><code>python -m worker.worker</code></div>}</div></div>
      </section>
    </div>
  );
}

function Metric({ icon, label, value, note, accent = false }: { icon: React.ReactNode; label: string; value: string | number; note: string; accent?: boolean }) {
  return <div className="metric-card"><div className="metric-icon">{icon}</div><div><div className="eyebrow">{label}</div><div className={accent ? "metric-value text-acid" : "metric-value"}>{value}</div><div className="metric-note">{note}</div></div></div>;
}