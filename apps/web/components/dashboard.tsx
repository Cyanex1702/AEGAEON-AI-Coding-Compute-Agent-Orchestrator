"use client";

import {
  ArrowUpRight,
  Boxes,
  CheckCircle2,
  Cpu,
  FolderKanban,
  Gauge,
  Plus,
  Radio,
  Sparkles,
} from "lucide-react";
import type { Activity, DashboardStats, Project, Worker } from "@/lib/types";
import { ActivityFeed, Badge, MiniBar, StatusDot, timeAgo } from "./ui";

type Props = {
  stats: DashboardStats;
  projects: Project[];
  workers: Worker[];
  events: Activity[];
  onProject: (id: string) => void;
  onCreate: () => void;
};

export function Dashboard({ stats, projects, workers, events, onProject, onCreate }: Props) {
  const active = projects.filter((item) => !["completed", "failed", "cancelled"].includes(item.status));
  const recent = projects.slice(0, 5);
  const onlineWorkers = workers.filter((item) => item.status !== "offline");
  return (
    <div className="page-wrap">
      <div className="page-heading">
        <div>
          <div className="eyebrow mb-2">Command center</div>
          <h1>Good evening, operator.</h1>
          <p>Plan, dispatch, and verify autonomous software builds from one local control plane.</p>
        </div>
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
          <div className="launch-orbit orbit-one" />
          <div className="launch-orbit orbit-two" />
          <div className="relative z-10 max-w-xl">
            <Badge tone="acid"><Sparkles className="mr-1.5 h-3 w-3" /> Demo mode online</Badge>
            <h2>Your compute fabric is waiting.</h2>
            <p>Start with the calculator API demo. AEGAEON will plan the DAG, dispatch code jobs, apply Git patches, run pytest, and review the final repository.</p>
            <button className="primary-button mt-6" onClick={onCreate}>Create first project <ArrowUpRight className="h-4 w-4" /></button>
          </div>
          <div className="launch-node"><Boxes className="h-6 w-6" /><span>PLANNER</span></div>
        </section>
      )}

      <section className="dashboard-grid">
        <div className="panel overflow-hidden lg:col-span-2">
          <div className="panel-header">
            <div><div className="eyebrow">Build queue</div><h3>Projects</h3></div>
            <span className="text-xs text-zinc-600">{active.length} active</span>
          </div>
          <div className="project-table">
            {recent.length ? recent.map((project) => (
              <button className="project-table-row" key={project.id} onClick={() => onProject(project.id)}>
                <div className="project-avatar">{project.name.slice(0, 2).toUpperCase()}</div>
                <div className="min-w-0 flex-1 text-left">
                  <div className="truncate text-sm font-medium text-zinc-100">{project.name}</div>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-zinc-600">
                    <span>{project.strategy.replaceAll("_", " ")}</span><span>·</span><span>{timeAgo(project.updated_at)}</span>
                  </div>
                </div>
                <div className="hidden w-32 sm:block"><MiniBar value={project.progress} /><span className="mt-1.5 block text-right text-[10px] text-zinc-600">{project.progress}%</span></div>
                <Badge tone={project.status === "completed" ? "acid" : project.status === "failed" ? "danger" : "cyan"}><StatusDot status={project.status} /> {project.status}</Badge>
              </button>
            )) : <div className="empty-state py-16">No projects in the queue.</div>}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header"><div><div className="eyebrow">Event stream</div><h3>Live activity</h3></div><StatusDot status="online" pulse /></div>
          <ActivityFeed events={events.slice(0, 7)} empty="Events appear here while a project runs." />
        </div>

        <div className="panel lg:col-span-3">
          <div className="panel-header"><div><div className="eyebrow">Compute mesh</div><h3>Workers</h3></div><Badge>{onlineWorkers.length} connected</Badge></div>
          <div className="worker-strip">
            {onlineWorkers.length ? onlineWorkers.map((worker) => (
              <div className="worker-chip" key={worker.id}>
                <div className="worker-icon"><Cpu className="h-4 w-4" /></div>
                <div className="min-w-0 flex-1"><div className="flex items-center gap-2 text-sm text-zinc-200"><span className="truncate">{worker.hostname}</span><StatusDot status={worker.status} pulse={worker.status === "busy"} /></div><div className="mt-1 text-[10px] uppercase tracking-wider text-zinc-600">{worker.hardware.gpu?.available ? worker.hardware.gpu.name : `${worker.hardware.cpu_cores ?? 1} CPU cores`}</div></div>
                <div className="w-20"><MiniBar value={worker.cpu_percent} tone="cyan" /><span className="mt-1 block text-right text-[10px] text-zinc-600">{worker.cpu_percent.toFixed(0)}% load</span></div>
              </div>
            )) : (
              <div className="worker-empty"><Gauge className="h-5 w-5" /><span>No external workers. Local demo runtime will execute builds.</span><code>python -m worker.worker</code></div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function Metric({ icon, label, value, note, accent = false }: { icon: React.ReactNode; label: string; value: string | number; note: string; accent?: boolean }) {
  return (
    <div className="metric-card">
      <div className="metric-icon">{icon}</div>
      <div><div className="eyebrow">{label}</div><div className={accent ? "metric-value text-acid" : "metric-value"}>{value}</div><div className="metric-note">{note}</div></div>
    </div>
  );
}

