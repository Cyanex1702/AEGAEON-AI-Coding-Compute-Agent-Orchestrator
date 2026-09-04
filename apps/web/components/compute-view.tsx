"use client";

import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  Cpu,
  MemoryStick,
  Route,
  Server,
} from "lucide-react";
import type {
  ComputeIncident,
  ComputeOverview,
  ComputeWorker,
  ModelExecutionPlan,
} from "@/lib/types";
import { Badge, cn, exactTime, StatusDot, timeAgo } from "./ui";

export function ComputeView({ compute }: { compute: ComputeOverview | null }) {
  if (!compute) {
    return (
      <div className="page-wrap">
        <div className="empty-worker-state panel">
          <BrainCircuit className="h-7 w-7 text-cyan" />
          <h3>Compute Intelligence is synchronizing</h3>
          <p>Worker memory, execution plans, and model history will appear here.</p>
        </div>
      </div>
    );
  }

  const total = compute.summary.total_vram_mb;
  const free = compute.summary.free_vram_mb;
  const usedPercent = total ? ((total - free) / total) * 100 : 0;

  return (
    <div className="page-wrap">
      <div className="page-heading">
        <div>
          <div className="eyebrow mb-2">Adaptive compute intelligence</div>
          <h1>Broke Boy Compute</h1>
          <p>Live GPU headroom, preflight decisions, learned history, and OOM recovery.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="cyan"><BrainCircuit className="h-3 w-3" /> {compute.policy.preset.replaceAll("_", " ")}</Badge>
          <Badge tone={compute.summary.active_incidents ? "warning" : "acid"}>
            <StatusDot status={compute.summary.active_incidents ? "running" : "online"} pulse={Boolean(compute.summary.active_incidents)} />
            {compute.summary.active_incidents ? `${compute.summary.active_incidents} recovering` : "Healthy"}
          </Badge>
        </div>
      </div>

      <div className="worker-overview-grid">
        <ComputeStat icon={<Server />} value={compute.summary.online_workers} label="Online workers" />
        <ComputeStat icon={<Cpu />} value={compute.summary.gpu_count} label="GPUs visible" />
        <ComputeStat icon={<MemoryStick />} value={formatMemory(free)} label={`Free of ${formatMemory(total)} VRAM`} />
        <ComputeStat icon={<AlertTriangle />} value={compute.summary.historical_ooms} label="Historical OOMs" />
      </div>

      <section className="panel p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="eyebrow">Current policy</div>
            <h3 className="mt-1 text-base text-zinc-100">{compute.policy.preset.replaceAll("_", " ")}</h3>
          </div>
          <div className="flex flex-wrap gap-2">
            {["QUALITY_FIRST", "BALANCED", "MEMORY_SAFE", "FAST"].map((preset) => (
              <span
                key={preset}
                className={cn(
                  "rounded-md border px-2.5 py-1 font-mono text-[9px] tracking-wide",
                  preset === compute.policy.preset
                    ? "border-cyan/50 bg-cyan/10 text-cyan"
                    : "border-white/[.06] text-zinc-600",
                )}
              >
                {preset.replaceAll("_", " ")}
              </span>
            ))}
          </div>
        </div>
        <div className="mt-5 grid gap-3 text-[11px] text-zinc-500 sm:grid-cols-2 lg:grid-cols-4">
          <PolicyFlag label="Model fallback" enabled={compute.policy.allow_model_fallback} />
          <PolicyFlag label="CPU offload" enabled={compute.policy.allow_cpu_offload} />
          <PolicyFlag label="Multi-GPU" enabled={compute.policy.allow_multi_gpu} />
          <PolicyFlag label="Quantization change" enabled={compute.policy.allow_quantization_change} />
        </div>
        <div className="mt-4">
          <MemoryBar value={usedPercent} />
          <div className="mt-2 flex justify-between text-[10px] text-zinc-600">
            <span>{formatMemory(total - free)} allocated / reserved</span>
            <span>{formatMemory(free)} live headroom</span>
          </div>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[1.05fr_.95fr]">
        <section className="panel overflow-hidden">
          <div className="panel-header">
            <div><div className="eyebrow">Compute fabric</div><h3>Live headroom</h3></div>
            <code className="endpoint-code">10s telemetry</code>
          </div>
          <div className="divide-y divide-white/[.05]">
            {compute.workers.length ? compute.workers.map((worker) => (
              <WorkerMemory key={worker.worker_id} worker={worker} />
            )) : (
              <div className="empty-state py-12">No compute worker telemetry yet.</div>
            )}
          </div>
        </section>

        <section className="panel overflow-hidden">
          <div className="panel-header">
            <div><div className="eyebrow">Recovery</div><h3>OOM incidents</h3></div>
            <Badge tone={compute.summary.active_incidents ? "warning" : "neutral"}>
              {compute.incidents.length} incidents
            </Badge>
          </div>
          <div className="divide-y divide-white/[.05]">
            {compute.incidents.length ? compute.incidents.slice(0, 6).map((incident) => (
              <IncidentRow key={incident.id} incident={incident} />
            )) : (
              <div className="empty-state py-12">No OOM incidents recorded.</div>
            )}
          </div>
        </section>
      </div>

      <section className="panel overflow-hidden">
        <div className="panel-header">
          <div><div className="eyebrow">Preflight planner</div><h3>Recent model execution plans</h3></div>
          <Badge tone="cyan"><Route className="h-3 w-3" /> {compute.active_plans.length} plans</Badge>
        </div>
        <div className="grid gap-3 p-4 lg:grid-cols-2">
          {compute.active_plans.length ? compute.active_plans.slice(0, 8).map((plan) => (
            <PlanCard key={plan.id} plan={plan} />
          )) : (
            <div className="empty-state col-span-full py-12">Plans appear when a Broke Boy task is prepared.</div>
          )}
        </div>
      </section>

      <section className="panel overflow-hidden">
        <div className="panel-header">
          <div><div className="eyebrow">Learned evidence</div><h3>Model and hardware history</h3></div>
          <Badge tone="neutral"><Activity className="h-3 w-3" /> {compute.observations.length} observations</Badge>
        </div>
        <div className="overflow-x-auto">
          <div className="min-w-[760px]">
            <div className="grid grid-cols-[1.5fr_.9fr_.7fr_.7fr_.7fr_.6fr] gap-3 border-b border-white/[.05] px-5 py-3 text-[9px] uppercase tracking-widest text-zinc-700">
              <span>Model / GPU</span><span>Runtime</span><span>Prompt</span><span>Output</span><span>Peak VRAM</span><span>Result</span>
            </div>
            {compute.observations.length ? compute.observations.slice(0, 12).map((item) => (
              <div key={item.id} className="grid grid-cols-[1.5fr_.9fr_.7fr_.7fr_.7fr_.6fr] gap-3 border-b border-white/[.04] px-5 py-3 text-[11px] text-zinc-400">
                <div className="min-w-0"><div className="truncate text-zinc-200">{item.model_id}</div><div className="mt-1 truncate text-[9px] text-zinc-700">{item.gpu_name}</div></div>
                <span>{item.runtime} · {item.quantization}</span>
                <span>{item.prompt_tokens.toLocaleString()}</span>
                <span>{item.generated_tokens.toLocaleString()} / {item.output_limit.toLocaleString()}</span>
                <span>{formatMemory(item.peak_vram_mb)}</span>
                <Badge tone={item.result === "SUCCESS" ? "acid" : "danger"}>{item.result}</Badge>
              </div>
            )) : <div className="empty-state py-12">Successful runs and memory failures will build a local evidence history.</div>}
          </div>
        </div>
      </section>
    </div>
  );
}

function WorkerMemory({ worker }: { worker: ComputeWorker }) {
  const free = worker.gpus.reduce((sum, gpu) => sum + gpu.free_mb, 0);
  const headroom = free >= 8192 ? "Good headroom" : free >= 4096 ? "Moderate" : free >= 2048 ? "Tight" : "Critical";
  return (
    <div className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm text-zinc-200"><StatusDot status={worker.status} pulse={worker.status === "busy"} /> <span className="truncate">{worker.hostname}</span></div>
          <div className="mt-1 truncate font-mono text-[9px] text-zinc-700">{worker.worker_id}</div>
        </div>
        <Badge tone={headroom === "Critical" ? "danger" : headroom === "Tight" ? "warning" : "acid"}>{headroom}</Badge>
      </div>
      {worker.gpus.map((gpu) => (
        <div className="mt-4" key={gpu.index}>
          <div className="flex justify-between text-[10px] text-zinc-500"><span>GPU {gpu.index} · {gpu.name}</span><span>{formatMemory(gpu.free_mb)} free</span></div>
          <MemoryBar value={gpu.total_mb ? ((gpu.total_mb - gpu.free_mb) / gpu.total_mb) * 100 : 0} />
          <div className="mt-1 text-[9px] text-zinc-700">{formatMemory(gpu.allocated_mb)} allocated · {formatMemory(gpu.reserved_mb)} reserved · {gpu.utilization_percent.toFixed(0)}% utilization</div>
        </div>
      ))}
      <div className="mt-4 grid grid-cols-2 gap-2 text-[10px] text-zinc-600">
        <span>RAM: {formatMemory(worker.ram_available_mb)} available</span>
        <span>Resident: {worker.resident_model ?? "none"}</span>
        <span>Safe prompt: {worker.max_recommended_prompt_tokens.toLocaleString()} tokens</span>
        <span>Safe output: {worker.max_recommended_output_tokens.toLocaleString()} tokens</span>
      </div>
    </div>
  );
}

function PlanCard({ plan }: { plan: ModelExecutionPlan }) {
  return (
    <article className="rounded-lg border border-white/[.06] bg-black/20 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><div className="truncate text-sm text-zinc-100">{plan.model_id}</div><div className="mt-1 truncate font-mono text-[9px] text-zinc-700">{plan.worker_id ?? "waiting for worker"}</div></div>
        <Badge tone={riskTone(plan.estimated_oom_risk)}>{plan.estimated_oom_risk} RISK</Badge>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 text-[10px] text-zinc-500">
        <span>Runtime <strong className="text-zinc-300">{plan.runtime}</strong></span>
        <span>Quant <strong className="text-zinc-300">{plan.quantization}</strong></span>
        <span>Prompt <strong className="text-zinc-300">{plan.prompt_token_budget.toLocaleString()}</strong></span>
        <span>Output <strong className="text-zinc-300">{plan.maximum_new_tokens.toLocaleString()}</strong></span>
        <span>Strategy <strong className="text-zinc-300">{plan.device_strategy.replaceAll("_", " ")}</strong></span>
        <span>Fit <strong className="text-zinc-300">{plan.fit_score}/100</strong></span>
      </div>
      <div className="mt-4 rounded-md bg-white/[.025] p-3 text-[10px] text-zinc-500">
        {plan.reasoning_summary[0] ?? "Adaptive preflight plan"}
      </div>
      {plan.next_recovery_action && <div className="mt-3 text-[10px] text-amber-200">Next recovery: {plan.next_recovery_action.replaceAll("_", " ")}</div>}
      {plan.context_plan?.dropped_sections.length ? <div className="mt-2 text-[9px] text-zinc-700">Trimmed: {plan.context_plan.dropped_sections.join(", ")}</div> : null}
    </article>
  );
}

function IncidentRow({ incident }: { incident: ComputeIncident }) {
  return (
    <div className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div><div className="text-xs text-zinc-200">{incident.classification.replaceAll("_", " ")}</div><div className="mt-1 font-mono text-[9px] text-zinc-700">{incident.root_failure_id}</div></div>
        <Badge tone={incident.state === "FAILED_FINAL" ? "danger" : incident.state === "RESOLVED" ? "acid" : "warning"}>{incident.state.replaceAll("_", " ")}</Badge>
      </div>
      <p className="mt-3 text-[11px] leading-5 text-zinc-500">{incident.message}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-[9px] text-zinc-600">
        <span>{incident.attempts.length} attempt{incident.attempts.length === 1 ? "" : "s"}</span>
        <span>·</span><span>{incident.model_id ?? "unknown model"}</span>
        {incident.recovery_action && <><span>·</span><span className="text-amber-300/80">{incident.recovery_action.replaceAll("_", " ")}</span></>}
      </div>
      <div className="mt-2 text-[9px] text-zinc-700" title={exactTime(incident.updated_at)}>Updated {timeAgo(incident.updated_at)}</div>
    </div>
  );
}

function ComputeStat({ icon, value, label }: { icon: React.ReactNode; value: string | number; label: string }) {
  return <div className="summary-card flex items-center gap-4"><div className="summary-icon">{icon}</div><div><div className="text-xl font-medium text-zinc-100">{value}</div><div className="mt-1 text-[10px] uppercase tracking-widest text-zinc-600">{label}</div></div></div>;
}

function PolicyFlag({ label, enabled }: { label: string; enabled: boolean }) {
  return <div className="flex items-center justify-between rounded-md border border-white/[.05] px-3 py-2"><span>{label}</span><span className={enabled ? "text-acid" : "text-zinc-700"}>{enabled ? "ALLOWED" : "LOCKED"}</span></div>;
}

function MemoryBar({ value }: { value: number }) {
  const safe = Math.max(0, Math.min(100, value));
  const color = safe >= 90 ? "bg-red-400" : safe >= 75 ? "bg-amber-300" : "bg-cyan";
  return <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[.05]"><div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${safe}%` }} /></div>;
}

function formatMemory(value: number) {
  return value >= 1024 ? `${(value / 1024).toFixed(value >= 10240 ? 0 : 1)} GB` : `${Math.max(0, Math.round(value))} MB`;
}

function riskTone(risk: string) {
  return risk === "LOW" ? "acid" : risk === "MODERATE" ? "cyan" : risk === "HIGH" ? "warning" : "danger";
}
