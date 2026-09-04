"use client";

import { useState } from "react";
import { BrainCircuit, CheckCircle2, FileLock2, GitBranch, Pencil, Save, Scale, Users } from "lucide-react";
import { api } from "@/lib/api";
import type { ProjectOrchestration } from "@/lib/types";
import { Badge, MiniBar } from "./ui";

type Props = {
  projectId: string;
  data: ProjectOrchestration | null;
  onChanged: (data: ProjectOrchestration) => void;
};

export function ArchitecturePanel({ projectId, data, onChanged }: Props) {
  const [strategy, setStrategy] = useState(
    data?.compute_plan?.strategy ?? "balanced",
  );
  const [workerCount, setWorkerCount] = useState(
    data?.compute_plan?.selected_worker_count ?? 1,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);


  if (!data?.analysis || !data.blueprint || !data.contract || !data.compute_plan) {
    return <section className="panel p-8 text-sm text-zinc-500">Project architecture is being prepared.</section>;
  }

  const verified = data.requirements.filter((item) => item.status === "VERIFIED").length;
  const completed = data.milestones.filter((item) => item.status === "COMPLETED").length;

  async function saveCompute() {
    setSaving(true);
    setError(null);
    try {
      onChanged(await api.updateComputePlan(projectId, strategy, workerCount));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update compute plan");
    } finally {
      setSaving(false);
    }
  }

  async function renameMilestone(id: string, title: string) {
    const next = window.prompt("Milestone title", title)?.trim();
    if (!next || next === title) return;
    setError(null);
    try {
      onChanged(await api.updateMilestone(projectId, id, { title: next }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update milestone");
    }
  }

  return (
    <section className="space-y-4">
      {error && <div className="error-banner">{error}</div>}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="panel p-5">
          <div className="flex items-center justify-between"><div className="eyebrow">Project analysis</div><Badge tone="cyan">{data.analysis.complexity}</Badge></div>
          <div className="mt-4 text-2xl font-medium text-zinc-100">{data.milestones.length} milestones</div>
          <div className="mt-2 text-xs text-zinc-500">{data.compute_plan.estimated_task_count} estimated tasks · {data.compute_plan.parallelizable_task_count} parallel</div>
          <div className="mt-4"><MiniBar value={data.milestones.length ? completed * 100 / data.milestones.length : 0} /></div>
        </div>
        <div className="panel p-5">
          <div className="eyebrow">Canonical blueprint</div>
          <div className="mt-4 space-y-2 text-xs text-zinc-400">{Object.entries(data.blueprint.architecture).map(([key, value]) => <div className="flex justify-between gap-3" key={key}><span className="capitalize text-zinc-600">{key}</span><span>{value}</span></div>)}</div>
          <div className="mt-4 flex flex-wrap gap-2">{data.blueprint.services.map((item) => <Badge key={item.name}>{item.name}</Badge>)}</div>
        </div>
        <div className="panel p-5">
          <div className="flex items-center justify-between"><div className="eyebrow">Compute plan</div><Users className="h-4 w-4 text-cyan-300" /></div>
          <div className="mt-3 text-xs text-zinc-500">Recommended: {data.compute_plan.recommended_worker_count} persistent worker(s)</div>
          <div className="mt-4 grid grid-cols-2 gap-2"><select value={strategy} onChange={(event) => setStrategy(event.target.value as typeof strategy)} disabled={saving}><option value="cheapest">Cheapest</option><option value="balanced">Balanced</option><option value="fast">Fast</option><option value="maximum_quality">Maximum quality</option></select><select value={workerCount} onChange={(event) => setWorkerCount(Number(event.target.value))} disabled={saving}>{Array.from({ length: 12 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1} worker{index ? "s" : ""}</option>)}</select></div>
          <button className="secondary-button mt-3" onClick={() => void saveCompute()} disabled={saving}><Save className="h-3.5 w-3.5" />{saving ? "Saving…" : "Save plan"}</button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_.75fr]">
        <div className="panel">
          <div className="panel-header"><div><div className="eyebrow">Milestone plan</div><h3>Continuous integration checkpoints</h3></div><Badge>{completed}/{data.milestones.length}</Badge></div>
          <div className="space-y-2 p-4">{data.milestones.map((milestone, index) => (
            <div className="rounded-lg border border-white/[.06] bg-black/20 p-4" key={milestone.id}>
              <div className="flex items-start gap-3"><div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-white/[.08] font-mono text-[10px] text-zinc-500">{index + 1}</div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><strong className="text-sm text-zinc-200">{milestone.title}</strong><Badge tone={milestone.status === "COMPLETED" ? "acid" : milestone.status === "RUNNING" ? "cyan" : "default"}>{milestone.status}</Badge></div><p className="mt-1 text-xs leading-5 text-zinc-500">{milestone.goal}</p><div className="mt-2 text-[10px] text-zinc-700">Contract v{milestone.starting_contract_version}{milestone.ending_contract_version ? ` → v${milestone.ending_contract_version}` : ""} · {milestone.completion_commit ? milestone.completion_commit.slice(0, 8) : "checkpoint pending"}</div></div>{["PENDING", "READY"].includes(milestone.status) && <button className="icon-button" title="Rename milestone" onClick={() => void renameMilestone(milestone.id, milestone.title)}><Pencil className="h-3.5 w-3.5" /></button>}</div>
              <div className="mt-3 flex flex-wrap gap-2">{milestone.acceptance_criteria.map((item) => <span key={item} className="rounded border border-white/[.05] px-2 py-1 text-[10px] text-zinc-600">{item}</span>)}</div>
              <div className="mt-3 space-y-1 border-t border-white/[.05] pt-3">{(data.milestone_tasks?.[milestone.id] ?? []).map((task) => <div className="flex items-center justify-between gap-3 text-[10px]" key={task.id}><span className="truncate text-zinc-500">{task.title}</span><Badge tone={task.status === "COMPLETED" ? "acid" : task.status === "FAILED" ? "danger" : task.status === "RUNNING" ? "cyan" : "default"}>{task.status}</Badge></div>)}</div>
            </div>
          ))}</div>
        </div>

        <div className="space-y-4">
          <div className="panel p-5"><div className="flex items-center justify-between"><div className="eyebrow">Project contract</div><Badge tone="acid">v{data.contract.version}</Badge></div><div className="mt-4 space-y-3 text-xs"><ContractRow label="Entities" value={Object.keys(data.contract.content.entities ?? {}).join(", ") || "None yet"} /><ContractRow label="Services" value={Object.keys(data.contract.content.services ?? {}).join(", ") || "ApplicationService"} /><ContractRow label="APIs" value={`${Object.keys(data.contract.content.api ?? {}).length} registered`} /><ContractRow label="Revisions" value={`${data.contract_revisions.length} durable`} /></div></div>
          <div className="panel p-5"><div className="flex items-center justify-between"><div className="eyebrow">Requirement coverage</div><Badge tone={verified === data.requirements.length ? "acid" : "warning"}>{verified}/{data.requirements.length}</Badge></div><div className="mt-4 space-y-2">{data.requirements.map((item) => <div className="flex gap-2 text-xs" key={item.id}>{item.status === "VERIFIED" ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-acid" /> : <Scale className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-700" />}<span className={item.status === "VERIFIED" ? "text-zinc-300" : "text-zinc-600"}>{item.text}</span></div>)}</div></div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header"><div><div className="eyebrow">Contract governance</div><h3>Worker proposals and Core decisions</h3></div><FileLock2 className="h-4 w-4 text-cyan-300" /></div>
        {data.contract_proposals.length ? <div className="grid gap-3 p-4 lg:grid-cols-3">{data.contract_proposals.map((proposal) => (
          <div className="rounded-lg border border-white/[.06] bg-black/20 p-4" key={proposal.id}>
            <div className="flex items-center justify-between gap-3"><Badge tone={proposal.status === "APPROVED" ? "acid" : proposal.status === "REJECTED" ? "danger" : "warning"}>{proposal.status}</Badge><span className="font-mono text-[9px] text-zinc-700">{proposal.proposal_type}</span></div>
            <div className="mt-3 break-all font-mono text-xs text-cyan-200">{proposal.target}</div>
            <p className="mt-2 text-[11px] leading-5 text-zinc-500">{proposal.reason}</p>
            <details className="mt-3 text-[10px] text-zinc-600"><summary className="cursor-pointer">Proposed change</summary><pre className="mt-2 max-h-40 overflow-auto rounded bg-black/40 p-2 text-[9px] text-zinc-500">{JSON.stringify(proposal.proposed_value ?? proposal.change, null, 2)}</pre></details>
            <div className="mt-3 border-t border-white/[.05] pt-3 text-[10px] leading-4 text-zinc-600">{proposal.review_reason || "Awaiting Lead review"}<div className="mt-1 font-mono text-zinc-700">{proposal.worker_id ?? "Core"} · {proposal.task_id ?? "project"}</div></div>
          </div>
        ))}</div> : <div className="p-6 text-xs text-zinc-600">No contract proposals have been submitted. Canonical files remain Core-owned.</div>}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="panel"><div className="panel-header"><div><div className="eyebrow">Lead activity</div><h3>Decisions and evidence</h3></div><BrainCircuit className="h-4 w-4 text-cyan-300" /></div><div className="space-y-2 p-4">{data.lead_decisions.slice(0, 8).map((decision) => <div className="rounded border border-white/[.05] p-3" key={decision.id}><div className="text-xs font-medium text-zinc-300">{decision.summary}</div><p className="mt-1 text-[11px] leading-5 text-zinc-600">{decision.rationale}</p></div>)}</div></div>
        <div className="panel"><div className="panel-header"><div><div className="eyebrow">Architecture decisions</div><h3>ADR register</h3></div><GitBranch className="h-4 w-4 text-cyan-300" /></div><div className="space-y-2 p-4">{data.architecture_decisions.map((decision) => <div className="rounded border border-white/[.05] p-3" key={decision.id}><div className="flex justify-between gap-3 text-xs"><strong className="text-zinc-300">{decision.topic}</strong><span className="text-zinc-700">v{decision.contract_version}</span></div><div className="mt-1 text-[11px] text-cyan-200/70">{decision.decision}</div><p className="mt-1 text-[10px] leading-4 text-zinc-600">{decision.reason}</p></div>)}</div></div>
      </div>
    </section>
  );
}

function ContractRow({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between gap-4"><span className="text-zinc-600">{label}</span><span className="max-w-[65%] truncate text-right text-zinc-300" title={value}>{value}</span></div>;
}


