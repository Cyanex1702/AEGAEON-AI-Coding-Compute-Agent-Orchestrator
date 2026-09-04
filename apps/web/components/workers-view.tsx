"use client";

import { Cpu, HardDrive, Microchip, Radio, Server } from "lucide-react";
import type { Worker } from "@/lib/types";
import { Badge, exactTime, MiniBar, StatusDot, timeAgo } from "./ui";

export function WorkersView({ workers }: { workers: Worker[] }) {
  const online = workers.filter((worker) => worker.status !== "offline");
  return (
    <div className="page-wrap">
      <div className="page-heading"><div><div className="eyebrow mb-2">Compute fabric</div><h1>Workers</h1><p>Disposable execution nodes connected to the controller.</p></div><Badge tone="acid"><Radio className="h-3 w-3" /> {online.length} online</Badge></div>
      <div className="worker-overview-grid"><WorkerStat icon={<Server />} value={workers.length} label="Registered" /><WorkerStat icon={<Cpu />} value={online.length} label="Available" /><WorkerStat icon={<Microchip />} value={workers.filter((worker) => worker.hardware.gpu?.available).length} label="GPU nodes" /><WorkerStat icon={<HardDrive />} value={`${Math.round(online.reduce((sum, worker) => sum + (worker.hardware.ram_mb ?? 0), 0) / 1024)} GB`} label="Total memory" /></div>
      <section className="panel overflow-hidden">
        <div className="panel-header"><div><div className="eyebrow">Registry</div><h3>Connected machines</h3></div><code className="endpoint-code">WS /ws/worker</code></div>
        {workers.length ? <div className="workers-table"><div className="workers-table-head"><span>Worker</span><span>Status</span><span>Compute</span><span>Utilization</span><span>Models</span><span>Heartbeat</span></div>{workers.map((worker) => <div className="workers-table-row" key={worker.id}><div className="min-w-0"><div className="truncate text-sm font-medium text-zinc-200">{worker.hostname}</div><div className="mt-1 truncate font-mono text-[10px] text-zinc-700">{worker.id}</div></div><div><Badge tone={worker.status === "offline" ? "danger" : worker.status === "busy" ? "cyan" : "acid"}><StatusDot status={worker.status} pulse={worker.status === "busy"} />{worker.status}</Badge>{worker.stage && <div className="mt-1 text-[10px] text-cyan-500/70">{worker.stage}{worker.current_task ? ` · ${worker.current_task}` : ""}</div>}{worker.status === "busy" && worker.progress_percent > 0 && <div className="mt-2"><MiniBar value={worker.progress_percent} tone="cyan" /></div>}</div><div><div className="text-xs text-zinc-300">{worker.hardware.gpu?.available ? worker.hardware.gpu.name : `${worker.hardware.cpu_cores} CPU cores`}</div><div className="mt-1 text-[10px] text-zinc-600">{Math.round((worker.hardware.ram_mb ?? 0) / 1024)} GB RAM{worker.hardware.gpu?.available ? ` · ${Math.round((worker.hardware.gpu.vram_mb ?? 0) / 1024)} GB VRAM` : ""}</div></div><div className="space-y-2"><Usage label="CPU" value={worker.cpu_percent} /><Usage label="RAM" value={worker.ram_percent} /></div><div className="tag-list">{worker.models.length ? worker.models.map((model) => <span key={model.id} title={model.last_error ?? undefined}>{model.id}{model.runtime ? ` · ${model.runtime}` : ""}{model.quantization ? ` · ${model.quantization}` : ""}{model.status ? ` · ${model.status}` : ""}</span>) : <span>runtime only</span>}</div><div className="text-xs text-zinc-600" title={exactTime(worker.last_heartbeat)}>{timeAgo(worker.last_heartbeat)}</div></div>)}</div> : <div className="empty-worker-state"><div className="empty-worker-icon"><Cpu className="h-6 w-6" /></div><h3>No external workers connected</h3><p>Create a Broke Boy project to generate secure Run-All Colab notebooks, or start a local worker.</p><code>python -m worker.worker --controller ws://127.0.0.1:8000/ws/worker</code></div>}
      </section>
    </div>
  );
}

function WorkerStat({ icon, value, label }: { icon: React.ReactNode; value: string | number; label: string }) { return <div className="summary-card flex items-center gap-4"><div className="summary-icon">{icon}</div><div><div className="text-xl font-medium text-zinc-100">{value}</div><div className="mt-1 text-[10px] uppercase tracking-widest text-zinc-600">{label}</div></div></div>; }
function Usage({ label, value }: { label: string; value: number }) { return <div className="grid grid-cols-[26px_1fr_28px] items-center gap-2 text-[9px] text-zinc-700"><span>{label}</span><MiniBar value={value} tone="cyan" /><span className="text-right">{value.toFixed(0)}%</span></div>; }


