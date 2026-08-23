"use client";

import {
  Boxes,
  ChevronRight,
  Cpu,
  FolderKanban,
  LayoutDashboard,
  Network,
  Plus,
  Settings2,
} from "lucide-react";
import type { Project, Worker } from "@/lib/types";
import { cn, StatusDot } from "./ui";

export type AppView = "dashboard" | "project" | "workers" | "models" | "settings";

type Props = {
  view: AppView;
  projects: Project[];
  workers: Worker[];
  selectedProjectId: string | null;
  onView: (view: AppView) => void;
  onProject: (id: string) => void;
  onCreate: () => void;
};

export function Sidebar({
  view,
  projects,
  workers,
  selectedProjectId,
  onView,
  onProject,
  onCreate,
}: Props) {
  const navigation = [
    { id: "dashboard" as const, label: "Overview", icon: LayoutDashboard },
    { id: "workers" as const, label: "Compute workers", icon: Cpu },
    { id: "models" as const, label: "Model registry", icon: Boxes },
  ];
  return (
    <aside className="sidebar">
      <div className="brand-block">
        <div className="brand-mark"><Network className="h-4 w-4" /></div>
        <div>
          <div className="brand-name">AEGAEON</div>
          <div className="brand-sub">AGENTIC COMPUTE</div>
        </div>
      </div>

      <button className="new-project-button" onClick={onCreate}>
        <Plus className="h-4 w-4" />
        New project
        <span className="ml-auto text-[10px] text-zinc-600">⌘ N</span>
      </button>

      <nav className="nav-block">
        <div className="eyebrow px-3">Workspace</div>
        {navigation.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              className={cn("nav-item", view === item.id && "nav-item-active")}
              onClick={() => onView(item.id)}
            >
              <Icon className="h-4 w-4" />
              <span>{item.label}</span>
              {item.id === "workers" && (
                <span className="nav-count">{workers.filter((worker) => worker.status !== "offline").length}</span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="nav-block min-h-0 flex-1">
        <div className="mb-2 flex items-center justify-between px-3">
          <div className="eyebrow">Projects</div>
          <span className="text-[10px] tabular-nums text-zinc-600">{projects.length}</span>
        </div>
        <div className="project-nav-list">
          {projects.length === 0 ? (
            <button className="empty-project" onClick={onCreate}>
              <FolderKanban className="h-5 w-5" />
              Create your first build
            </button>
          ) : (
            projects.slice(0, 8).map((project) => (
              <button
                key={project.id}
                className={cn(
                  "project-nav-item",
                  view === "project" && selectedProjectId === project.id && "project-nav-active",
                )}
                onClick={() => onProject(project.id)}
              >
                <StatusDot
                  status={project.status}
                  pulse={["running", "testing", "reviewing", "planning"].includes(project.status)}
                />
                <span className="min-w-0 flex-1 truncate">{project.name}</span>
                <ChevronRight className="h-3 w-3 text-zinc-700" />
              </button>
            ))
          )}
        </div>
      </div>

      <button className="nav-item mt-auto" onClick={() => onView("settings")}>
        <Settings2 className="h-4 w-4" />
        <span>Runtime settings</span>
      </button>
      <div className="local-mode">
        <div className="flex items-center gap-2"><StatusDot status="online" pulse /> Local-first</div>
        <span>v0.1.0</span>
      </div>
    </aside>
  );
}

