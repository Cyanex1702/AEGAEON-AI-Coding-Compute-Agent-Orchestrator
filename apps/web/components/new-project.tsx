"use client";

import { useState } from "react";
import { BrainCircuit, Check, Cpu, FlaskConical, Sparkles, WalletCards, X, Zap } from "lucide-react";
import { api } from "@/lib/api";
import type { Project } from "@/lib/types";
import { RemoteConnectivityPanel } from "./remote-connectivity";
import { cn } from "./ui";

const demoPrompt = `Build a Python calculator API with FastAPI.
Include add, subtract, multiply and divide endpoints.
Add pytest tests.`;

type Props = { onClose: () => void; onCreated: (project: Project) => void };
type Mode = "rich_boy" | "broke_boy";

export function NewProject({ onClose, onCreated }: Props) {
  const [prompt, setPrompt] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("rich_boy");
  const [intelligence, setIntelligence] = useState(70);
  const [retries, setRetries] = useState(3);
  const [runTests, setRunTests] = useState(true);
  const [review, setReview] = useState(true);
  const [retryFailed, setRetryFailed] = useState(true);
  const [workerCount, setWorkerCount] = useState(1);
  const [computeTarget, setComputeTarget] = useState<"google_colab" | "kaggle" | "local_gpu" | "cloud_gpu">("google_colab");
  const [targetVram, setTargetVram] = useState(15000);
  const [quantization, setQuantization] = useState<"4bit" | "8bit" | "bf16">("4bit");
  const [modelSelection, setModelSelection] = useState<"recommend" | "manual">("recommend");
  const [selectedModel, setSelectedModel] = useState("");
  const [researchLevel, setResearchLevel] = useState<"standard" | "manual">("standard");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (prompt.trim().length < 10) return setError("Describe the project in at least 10 characters.");
    if (mode === "broke_boy" && modelSelection === "manual" && !selectedModel.trim()) {
      return setError("Enter a Hugging Face model ID or choose automatic recommendation.");
    }
    setSubmitting(true);
    setError(null);
    try {
      const project = await api.createProject({
        name: name.trim() || undefined,
        prompt,
        strategy: "auto",
        intelligence,
        maximum_retries: retries,
        run_tests: runTests,
        review_code: review,
        retry_failed_tasks: retryFailed,
        execution_mode: mode,
        worker_count: workerCount,
        compute_target: computeTarget,
        model_selection: modelSelection,
        selected_model: selectedModel.trim() || undefined,
        target_vram_mb: targetVram,
        quantization,
        research_level: researchLevel,
      });
      if (mode === "rich_boy") await api.runProject(project.id);
      onCreated(project);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create project");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="new-project-modal">
        <div className="modal-header">
          <div><div className="eyebrow mb-2">Initialize build</div><h2>New project</h2><p>Choose who pays for intelligence, then describe the outcome.</p></div>
          <button className="icon-button" onClick={onClose}><X className="h-4 w-4" /></button>
        </div>
        <div className="modal-scroll">
          <div className="field">
            <div className="flex items-center justify-between"><label>What do you want to build?</label><button className="demo-fill" onClick={() => { setPrompt(demoPrompt); setName("Calculator API"); }}><FlaskConical className="h-3 w-3" /> Use demo brief</button></div>
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Build a tested application…" autoFocus />
            <div className="field-footer"><span>Detailed requirements work best</span><span>{prompt.length.toLocaleString()} / 20,000</span></div>
          </div>
          <div className="field"><label>Project name <span>optional</span></label><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Derived automatically from your prompt" /></div>

          <div className="field"><label>Execution mode</label>
            <div className="grid gap-3 sm:grid-cols-2">
              <ModeCard active={mode === "rich_boy"} onClick={() => setMode("rich_boy")} icon={<Zap />} title="Rich Boy" badge="Fastest" detail="Use the configured provider. AEGAEON plans and codes immediately." />
              <ModeCard active={mode === "broke_boy"} onClick={() => setMode("broke_boy")} icon={<WalletCards />} title="Broke Boy" badge="Wallet-safe" detail="Run an open coding model on your own Colab GPU worker." />
            </div>
          </div>

          {mode === "broke_boy" && <div className="rounded-lg border border-cyan-300/10 bg-cyan-300/[.025] p-4">
            <div className="mb-4"><div className="eyebrow">Worker setup</div><p className="mt-1 text-xs text-zinc-500">AEGAEON will recommend a model that fits your GPU, then generate Run-All notebooks.</p></div>
            <div className="form-grid">
              <div className="field"><label>Compute</label><select value={computeTarget} onChange={(event) => setComputeTarget(event.target.value as typeof computeTarget)}><option value="google_colab">Google Colab</option><option value="kaggle">Kaggle</option><option value="local_gpu">Local GPU</option><option value="cloud_gpu">Cloud GPU</option></select></div>
              <div className="field"><label>Workers</label><div className="stepper"><button onClick={() => setWorkerCount(Math.max(1, workerCount - 1))}>−</button><strong>{workerCount}</strong><button onClick={() => setWorkerCount(Math.min(12, workerCount + 1))}>+</button></div></div>
              <div className="field"><label>Target GPU</label><select value={targetVram} onChange={(event) => setTargetVram(Number(event.target.value))}><option value={15000}>T4 · 15 GB</option><option value={24000}>L4 / A10 · 24 GB</option><option value={40000}>A100 · 40 GB</option><option value={80000}>A100 · 80 GB</option></select></div>
              <div className="field"><label>Quantization</label><select value={quantization} onChange={(event) => setQuantization(event.target.value as typeof quantization)}><option value="4bit">4-bit · recommended</option><option value="8bit">8-bit</option><option value="bf16">BF16</option></select></div>
              <div className="field"><label>Model selection</label><select value={modelSelection} onChange={(event) => setModelSelection(event.target.value as typeof modelSelection)}><option value="recommend">Evidence-backed recommendation</option><option value="manual">Manual model ID</option></select></div>
            </div>
            {modelSelection === "manual" && <div className="field mt-3"><label>Hugging Face model ID</label><input value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)} placeholder="Qwen/Qwen2.5-Coder-1.5B-Instruct" /></div>}
<div className="field mt-3"><label>Research level</label><select value={researchLevel} onChange={(event) => setResearchLevel(event.target.value as typeof researchLevel)}><option value="standard">Standard · local evidence</option><option value="manual">Manual advisor prompt</option></select></div>
            {computeTarget !== "local_gpu" && (
              <div className="mt-4 rounded-lg border border-white/[.06] bg-black/20 p-4">
                <div className="eyebrow mb-3">Remote connection</div>
                <RemoteConnectivityPanel />
              </div>
            )}
          </div>}

          <div className="form-grid">
            <div className="field"><label>Maximum retries</label><div className="stepper"><button onClick={() => setRetries(Math.max(0, retries - 1))}>−</button><strong>{retries}</strong><button onClick={() => setRetries(Math.min(10, retries + 1))}>+</button></div></div>
            <div className="intelligence-field"><div className="flex items-center justify-between"><div><label>Intelligence</label><p>Planning depth.</p></div><span>{intelligence}%</span></div><input type="range" min="0" max="100" value={intelligence} onChange={(event) => setIntelligence(Number(event.target.value))} style={{ "--range": `${intelligence}%` } as React.CSSProperties} /></div>
          </div>
          <div className="toggle-list"><Toggle checked={runTests} onChange={setRunTests} title="Run tests automatically" detail="Execute detected tests and build commands" /><Toggle checked={review} onChange={setReview} title="Review generated code" detail="Inspect diffs and verification evidence" /><Toggle checked={retryFailed} onChange={setRetryFailed} title="Retry failed tasks" detail="Use a bounded repair loop" /></div>
          {error && <div className="error-banner"><X className="h-4 w-4" />{error}</div>}
        </div>
        <div className="modal-footer"><div className="flex items-center gap-2 text-[11px] text-zinc-600"><span className="h-1.5 w-1.5 rounded-full bg-acid" /> {mode === "rich_boy" ? "Provider route" : "No provider spend"}</div><div className="flex gap-2"><button className="secondary-button" onClick={onClose}>Cancel</button><button className={cn("primary-button", submitting && "opacity-60")} onClick={submit} disabled={submitting}><Sparkles className={cn("h-4 w-4", submitting && "animate-spin")} />{submitting ? "Initializing…" : mode === "rich_boy" ? "Build project" : "Create worker setup"}</button></div></div>
      </div>
    </div>
  );
}

function ModeCard({ active, onClick, icon, title, badge, detail }: { active: boolean; onClick: () => void; icon: React.ReactNode; title: string; badge: string; detail: string }) {
  return <button onClick={onClick} className={cn("rounded-lg border p-4 text-left transition", active ? "border-acid/50 bg-acid/[.06]" : "border-white/[.07] bg-black/20 hover:border-white/[.14]")}><span className="flex items-center justify-between"><span className={cn("summary-icon", active && "text-acid")}>{icon}</span><span className="text-[9px] uppercase tracking-widest text-zinc-600">{badge}</span></span><strong className="mt-3 block text-sm text-zinc-100">{title}</strong><small className="mt-1 block leading-5 text-zinc-500">{detail}</small></button>;
}

function Toggle({ checked, onChange, title, detail }: { checked: boolean; onChange: (checked: boolean) => void; title: string; detail: string }) { return <button className="toggle-row" onClick={() => onChange(!checked)}><span className={cn("checkbox", checked && "checkbox-active")}>{checked && <Check className="h-3 w-3" strokeWidth={3} />}</span><span className="text-left"><strong>{title}</strong><small>{detail}</small></span></button>; }