"use client";

import { useEffect, useMemo, useState } from "react";
import { Bell, Command, Menu, Search, Wifi, WifiOff, X } from "lucide-react";
import { Dashboard } from "./dashboard";
import { ComputeView } from "./compute-view";
import { ModelsView } from "./models-view";
import { NewProject } from "./new-project";
import { ProjectView } from "./project-view";
import { SettingsView } from "./settings-view";
import { type AppView, Sidebar } from "./sidebar";
import { StatusDot } from "./ui";
import { WorkersView } from "./workers-view";
import { useLiveData } from "@/hooks/use-live-data";
import type { Project } from "@/lib/types";

export function AegaeonShell() {
  const data = useLiveData();
  const [view, setView] = useState<AppView>("dashboard");
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const selectedProject = useMemo(() => data.projects.find((item) => item.id === selectedProjectId) ?? null, [data.projects, selectedProjectId]);

  useEffect(() => {
    function keyboard(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") {
        event.preventDefault();
        setCreateOpen(true);
      }
    }
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, []);

  function openProject(id: string) {
    setSelectedProjectId(id);
    setView("project");
    setSidebarOpen(false);
  }

  function projectCreated(project: Project) {
    setCreateOpen(false);
    setSelectedProjectId(project.id);
    setView("project");
    void data.refresh();
  }

  return (
    <main className="app-shell">
      <div className={sidebarOpen ? "mobile-sidebar mobile-sidebar-open" : "mobile-sidebar"}>
        <Sidebar view={view} projects={data.projects} workers={data.workers} selectedProjectId={selectedProjectId} onView={(next) => { setView(next); setSidebarOpen(false); }} onProject={openProject} onCreate={() => setCreateOpen(true)} />
      </div>
      {sidebarOpen && <button className="mobile-overlay" onClick={() => setSidebarOpen(false)} aria-label="Close sidebar" />}
      <div className="desktop-sidebar"><Sidebar view={view} projects={data.projects} workers={data.workers} selectedProjectId={selectedProjectId} onView={setView} onProject={openProject} onCreate={() => setCreateOpen(true)} /></div>

      <section className="main-column">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setSidebarOpen(true)}><Menu className="h-4 w-4" /></button>
          <div className="topbar-context"><span className="hidden text-zinc-700 sm:inline">Workspace</span><span className="hidden text-zinc-800 sm:inline">/</span><span>{view === "project" ? selectedProject?.name ?? "Project" : view[0].toUpperCase() + view.slice(1)}</span></div>
          <div className="topbar-search"><Search className="h-3.5 w-3.5" /><span>Search projects, tasks, workers</span><kbd><Command className="h-2.5 w-2.5" /> K</kbd></div>
          <div className="ml-auto flex items-center gap-2">
            <div className={data.connected ? "connection-pill" : "connection-pill connection-offline"}>{data.connected ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}<span className="hidden sm:inline">{data.connected ? "Live" : "Offline"}</span></div>
            <button className="icon-button relative"><Bell className="h-4 w-4" />{data.events.length > 0 && <span className="notification-dot" />}</button>
            <div className="operator-avatar">OP</div>
          </div>
        </header>

        <div className="content-area">
          {data.error && <div className="controller-error"><div><WifiOff className="h-4 w-4" /><span>Controller unavailable: {data.error}</span></div><code>uvicorn apps.controller.main:app --reload</code><button onClick={() => void data.refresh()}><StatusDot status="failed" /> Retry</button></div>}
          {data.loading ? <LoadingState /> : view === "dashboard" ? <Dashboard stats={data.stats} projects={data.projects} workers={data.workers} events={data.events} onProject={openProject} onCreate={() => setCreateOpen(true)} onRefresh={data.refresh} /> : view === "project" && selectedProject ? <ProjectView project={selectedProject} liveEvents={data.events} onRefresh={data.refresh} workers={data.workers} /> : view === "workers" ? <WorkersView workers={data.workers} /> : view === "compute" ? <ComputeView compute={data.compute} /> : view === "models" ? <ModelsView models={data.models} workers={data.workers} /> : view === "settings" ? <SettingsView /> : <Dashboard stats={data.stats} projects={data.projects} workers={data.workers} events={data.events} onProject={openProject} onCreate={() => setCreateOpen(true)} onRefresh={data.refresh} />}
        </div>
      </section>
      {createOpen && <NewProject onClose={() => setCreateOpen(false)} onCreated={projectCreated} />}
    </main>
  );
}

function LoadingState() { return <div className="loading-state"><div className="loading-mark"><X className="h-4 w-4 rotate-45" /></div><span>Synchronizing control plane</span></div>; }

