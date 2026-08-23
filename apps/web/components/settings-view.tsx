"use client";

import { useEffect, useState } from "react";
import {
  BrainCircuit,
  Check,
  Copy,
  Database,
  KeyRound,
  Laptop,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Integrations, ProviderStatus } from "@/lib/types";
import { RemoteConnectivityPanel } from "./remote-connectivity";
import { Badge, StatusDot } from "./ui";

export function SettingsView() {
  const [provider, setProvider] = useState<ProviderStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [integrations, setIntegrations] = useState<Integrations | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void Promise.all([api.providerStatus(), api.integrations()])
      .then(([providerStatus, integrationStatus]) => {
        setProvider(providerStatus);
        setIntegrations(integrationStatus);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  async function checkConnection() {
    setChecking(true);
    setError(null);
    try {
      setProvider(await api.checkProvider());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setChecking(false);
    }
  }

  const mode = provider?.mode ?? "demo";
  return (
    <div className="page-wrap">
      <div className="page-heading">
        <div>
          <div className="eyebrow mb-2">Configuration</div>
          <h1>Runtime settings</h1>
          <p>Local-first defaults and controller-side model connection status.</p>
        </div>
        <Badge tone={mode === "model" ? "cyan" : "acid"}>
          <Check className="h-3 w-3" /> {mode === "model" ? "Model mode" : "Demo mode"}
        </Badge>
      </div>

      <div className="settings-grid">
        <SettingsCard
          icon={<Database />}
          title="Canonical data"
          value="./data/projects"
          detail="Repositories, artifacts, logs, and orchestration metadata remain on the controller."
        />
        <SettingsCard
          icon={<KeyRound />}
          title="Worker authentication"
          value="AEGAEON_WORKER_TOKEN"
          detail="Notebooks redeem one-time pairing codes for short-lived worker credentials."
        />
        <SettingsCard
          icon={<Laptop />}
          title="Model endpoint"
          value={provider?.model ?? "Loading…"}
          detail={provider?.base_url ?? "Reading controller configuration…"}
        />
      </div>

      <section className="panel mt-5">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Provider connection</div>
            <h3>OpenAI-compatible model runtime</h3>
          </div>
          <button className="secondary-button" onClick={() => void checkConnection()} disabled={checking}>
            <RefreshCw className={checking ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
            {checking ? "Checking" : "Check connection"}
          </button>
        </div>
        <div className="grid gap-3 p-5 sm:grid-cols-2 lg:grid-cols-4">
          <RuntimeValue label="Provider" value={provider?.provider ?? "—"} />
          <RuntimeValue label="Model" value={provider?.model ?? "—"} />
          <RuntimeValue label="Structured output" value={provider?.structured_output_mode ?? "—"} />
          <RuntimeValue
            label="Reachability"
            value={
              <span className="flex items-center gap-2">
                <StatusDot status={provider?.reachable === true ? "online" : provider?.reachable === false ? "failed" : "pending"} />
                {provider?.reachable === true ? "Reachable" : provider?.reachable === false ? "Unavailable" : "Not checked"}
              </span>
            }
          />
        </div>
        {(provider?.error || error) && (
          <div className="mx-5 mb-5 rounded-md border border-red-400/20 bg-red-400/[.04] px-3 py-2 text-[10px] text-red-300">
            {provider?.error ?? error}
          </div>
        )}
      </section>

<section className="panel mt-5">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Integrations · Remote connectivity</div>
            <h3>Secure Colab worker connection</h3>
          </div>
        </div>
        <RemoteConnectivityPanel mode="settings" />
      </section>

      <section className="panel mt-5">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Quick connect</div>
            <h3>Start a disposable worker</h3>
          </div>
        </div>
        <CodeCopy code="python -m worker.worker --controller ws://127.0.0.1:8000/ws/worker --token development-token" />
      </section>

      <div className="security-note">
        <ShieldAlert className="h-5 w-5" />
        <div>
          <strong>Development sandbox</strong>
          <p>
            Rich Boy credentials stay on the controller; Broke Boy model weights and inference stay on workers. Generated code execution is path-confined,
            allowlisted, and timeout-bound, but it is not a hardened security boundary.
          </p>
        </div>
      </div>
    </div>
  );
}

function SettingsCard({
  icon,
  title,
  value,
  detail,
}: {
  icon: React.ReactNode;
  title: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="summary-card">
      <div className="summary-icon">{icon}</div>
      <div className="mt-5 text-xs uppercase tracking-widest text-zinc-600">{title}</div>
      <div className="mt-2 truncate font-mono text-sm text-zinc-200" title={value}>{value}</div>
      <p className="mt-3 text-xs leading-5 text-zinc-600">{detail}</p>
    </div>
  );
}

function RuntimeValue({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-[.16em] text-zinc-600">{label}</div>
      <div className="mt-2 font-mono text-[11px] text-zinc-300">{value}</div>
    </div>
  );
}

function CodeCopy({ code }: { code: string }) {
  return (
    <div className="code-copy">
      <code>{code}</code>
      <button onClick={() => navigator.clipboard.writeText(code)} aria-label="Copy worker command">
        <Copy className="h-4 w-4" />
      </button>
    </div>
  );
}
