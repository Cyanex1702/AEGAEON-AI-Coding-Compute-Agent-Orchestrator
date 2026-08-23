"use client";

import { useState } from "react";
import { BrainCircuit, Braces, Eye, Layers3, Search, ServerCog, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import type { ModelRecommendation, RegisteredModel, Worker } from "@/lib/types";
import { Badge, MiniBar, StatusDot } from "./ui";

export function ModelsView({ models, workers }: { models: RegisteredModel[]; workers: Worker[] }) {
  const [brief, setBrief] = useState("Build a production-ready tested web application with clear APIs.");
  const [vram, setVram] = useState(15000);
  const [quantization, setQuantization] = useState("4bit");
  const [recommendations, setRecommendations] = useState<ModelRecommendation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function recommend() {
    setLoading(true); setError(null);
    try { setRecommendations(await api.recommendModels(brief, vram, quantization)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Recommendation failed"); }
    finally { setLoading(false); }
  }

  return (
    <div className="page-wrap">
      <div className="page-heading"><div><div className="eyebrow mb-2">Intelligence layer</div><h1>Model Scout</h1><p>Hard hardware filters first, then explainable evidence-backed ranking.</p></div><Badge><ServerCog className="h-3 w-3" /> Open model evidence</Badge></div>

      <section className="panel mb-5 overflow-hidden">
        <div className="panel-header"><div><div className="eyebrow">Recommendation engine</div><h3>Find a wallet-safe coding model</h3></div><ShieldCheck className="h-4 w-4 text-acid" /></div>
        <div className="grid gap-3 p-5 lg:grid-cols-[1fr_160px_150px_auto]">
          <input className="rounded-md border border-white/[.08] bg-black/30 px-3 py-2 text-xs text-zinc-300 outline-none focus:border-cyan-300/30" value={brief} onChange={(event) => setBrief(event.target.value)} />
          <select value={vram} onChange={(event) => setVram(Number(event.target.value))}><option value={15000}>T4 · 15 GB</option><option value={24000}>L4 · 24 GB</option><option value={40000}>A100 · 40 GB</option></select>
          <select value={quantization} onChange={(event) => setQuantization(event.target.value)}><option value="4bit">4-bit</option><option value="8bit">8-bit</option><option value="bf16">BF16</option></select>
          <button className="primary-button" onClick={() => void recommend()} disabled={loading}><Search className="h-4 w-4" />{loading ? "Scoring…" : "Recommend"}</button>
        </div>
        {error && <div className="mx-5 mb-5 text-xs text-red-300">{error}</div>}
        {recommendations.length > 0 && <div className="grid gap-3 border-t border-white/[.05] p-5 lg:grid-cols-3">{recommendations.map((item, index) => <article key={item.model.id} className="rounded-lg border border-white/[.07] bg-black/20 p-4"><div className="flex items-center justify-between"><Badge tone={index === 0 ? "acid" : "neutral"}>{index === 0 ? "Recommended" : `#${index + 1}`}</Badge><strong className="text-lg text-zinc-100">{item.score}</strong></div><h3 className="mt-4 break-all text-sm">{item.model.id}</h3><p className="mt-1 text-xs text-zinc-600">{item.estimated_vram_mb.toLocaleString()} MB · {item.recommended_quantization} · {item.confidence} confidence</p><div className="mt-4"><MiniBar value={item.score} /></div><ul className="mt-4 space-y-1 text-[10px] leading-4 text-zinc-500">{item.reasons.map((reason) => <li key={reason}>• {reason}</li>)}</ul><a className="mt-4 inline-block text-[10px] text-cyan-300 hover:underline" href={item.model.source_url} target="_blank" rel="noreferrer">Official model card ↗</a></article>)}</div>}
      </section>

      <div className="eyebrow mb-3">Controller registry</div>
      <div className="models-grid">{models.map((model) => { const connected = workers.some((worker) => worker.models.some((item) => item.id === model.id)); const Icon = model.type === "vision" ? Eye : model.capabilities.includes("reasoning") ? BrainCircuit : Braces; return <article className="model-card" key={model.id}><div className="flex items-start justify-between"><div className="model-icon"><Icon className="h-5 w-5" /></div><Badge tone={connected || model.status.includes("demo") ? "acid" : "neutral"}><StatusDot status={connected || model.status.includes("demo") ? "online" : "offline"} />{connected ? "connected" : model.status}</Badge></div><h3>{model.name}</h3><p>{model.provider}</p><div className="model-specs"><div><span>Context</span><strong>{Math.round(model.context_length / 1024)}K</strong></div><div><span>Minimum VRAM</span><strong>{model.minimum_vram_mb ? `${Math.round(model.minimum_vram_mb / 1024)} GB` : "CPU"}</strong></div><div><span>Type</span><strong>{model.type}</strong></div></div><div className="tag-list mt-5">{model.capabilities.map((capability) => <span key={capability}>{capability}</span>)}</div></article>; })}</div>
      <section className="panel mt-5"><div className="panel-header"><div><div className="eyebrow">Routing model</div><h3>One complete model per worker</h3></div><Layers3 className="h-4 w-4 text-acid" /></div><div className="agent-route"><RouteNode name="Planner" detail="Deterministic DAG" /><div className="route-line" /><RouteNode name="Coder" detail="Typed model job" /><div className="route-line" /><RouteNode name="Verifier" detail="Controller tests" /><div className="route-line" /><RouteNode name="Reviewer" detail="Evidence gate" /></div></section>
    </div>
  );
}

function RouteNode({ name, detail }: { name: string; detail: string }) { return <div className="route-node"><div className="text-sm font-medium text-zinc-200">{name}</div><div className="mt-1 text-[10px] uppercase tracking-widest text-zinc-700">{detail}</div></div>; }