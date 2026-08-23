"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleStop,
  ExternalLink,
  KeyRound,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Wifi,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { RemoteConnectivity } from "@/lib/types";
import { Badge, StatusDot, cn } from "./ui";

type Props = {
  mode?: "launchpad" | "settings";
  onStatus?: (status: RemoteConnectivity) => void;
};

const ACTIVE_STATES = new Set([
  "checking",
  "starting_tunnel",
  "waiting_provider",
  "testing_https",
  "testing_websocket",
  "reconnecting",
]);

const LABELS: Record<RemoteConnectivity["state"], string> = {
  not_configured: "Not configured",
  checking: "Checking controller",
  starting_tunnel: "Starting secure connection",
  waiting_provider: "Waiting for provider",
  testing_https: "Testing HTTPS",
  testing_websocket: "Testing WebSocket",
  ready: "Ready",
  reconnecting: "Reconnecting",
  failed: "Failed",
  stopped: "Stopped",
};

export function RemoteConnectivityPanel({ mode = "launchpad", onStatus }: Props) {
  const [status, setStatus] = useState<RemoteConnectivity | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [manualUrl, setManualUrl] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  function apply(value: RemoteConnectivity) {
    setStatus(value);
    onStatus?.(value);
  }

  useEffect(() => {
    let active = true;
    const load = () =>
      api.remoteConnectivity()
        .then((value) => {
          if (active) apply(value);
        })
        .catch((reason: unknown) => {
          if (active) setError(reason instanceof Error ? reason.message : String(reason));
        });
    void load();
    const timer = window.setInterval(() => {
      if (status && ACTIVE_STATES.has(status.state)) void load();
    }, 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [onStatus]);

  async function run(action: () => Promise<RemoteConnectivity>) {
    setBusy(true);
    setError(null);
    try {
      apply(await action());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveToken() {
    if (!token.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.saveRemoteToken("ngrok", token.trim());
      setToken("");
      apply(await api.remoteConnectivity());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function removeToken() {
    setBusy(true);
    setError(null);
    try {
      await api.removeRemoteToken("ngrok");
      apply(await api.remoteConnectivity());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const ngrok = useMemo(
    () => status?.providers.find((provider) => provider.id === "ngrok"),
    [status],
  );
  const ready = status?.state === "ready" && status.rest_ok && status.websocket_ok;
  const active = status ? ACTIVE_STATES.has(status.state) : false;

  return (
    <div className={cn(mode === "settings" ? "p-5" : "")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <StatusDot status={ready ? "online" : status?.state === "failed" ? "failed" : "pending"} pulse={active} />
            <h3 className="text-sm font-medium text-zinc-200">{status ? LABELS[status.state] : "Checking"}</h3>
          </div>
          <p className="mt-2 max-w-xl text-xs leading-5 text-zinc-600">
            {ready
              ? "Remote Colab workers can reach this controller securely."
              : "AEGAEON checks and creates the secure route remote workers need."}
          </p>
        </div>
        {ready && <Badge tone="acid"><ShieldCheck className="h-3 w-3" /> verified</Badge>}
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-3">
        <CheckItem label="Controller healthy" done={Boolean(status)} active={status?.state === "checking"} />
        <CheckItem label="HTTPS connected" done={Boolean(status?.rest_ok)} active={status?.state === "testing_https"} />
        <CheckItem label="WebSocket connected" done={Boolean(status?.websocket_ok)} active={status?.state === "testing_websocket"} />
      </div>

      {status?.error && (
        <div className="mt-4 rounded-md border border-red-400/20 bg-red-400/[.04] px-3 py-2 text-xs leading-5 text-red-300">
          {status.error}
        </div>
      )}
      {error && <div className="mt-3 text-xs text-red-300">{error}</div>}

      {status?.stale_notebooks ? (
        <div className="mt-4 flex gap-2 rounded-md border border-amber-300/20 bg-amber-300/[.04] p-3 text-xs text-amber-200">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Remote address changed. {status.stale_notebooks} older notebook
            {status.stale_notebooks === 1 ? " is" : "s are"} stale. Generate fresh workers.
          </span>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        {!ready ? (
          <button
            className="primary-button"
            disabled={busy || active}
            onClick={() => void run(() => api.setupRemote({ provider: "auto" }))}
          >
            {busy || active ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Wifi className="h-4 w-4" />}
            {active ? LABELS[status?.state ?? "checking"] : "Set Up Automatically"}
          </button>
        ) : (
          <button className="secondary-button" disabled={busy} onClick={() => void run(api.restartRemote)}>
            <RefreshCw className={cn("h-4 w-4", busy && "animate-spin")} /> Restart connection
          </button>
        )}
        {status && status.state !== "stopped" && status.state !== "not_configured" && (
          <button className="secondary-button" disabled={busy} onClick={() => void run(api.stopRemote)}>
            <CircleStop className="h-4 w-4" /> Stop
          </button>
        )}
      </div>

      {mode === "settings" && (
        <div className="mt-6 border-t border-white/[.06] pt-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs font-medium text-zinc-300">ngrok connection</div>
              <div className="mt-1 text-[11px] text-zinc-600">
                Installed: {ngrok?.installed ? "Yes" : "No"} · Token: {ngrok?.token_configured ? "Saved securely" : "Not connected"}
              </div>
            </div>
            <Badge tone={ngrok?.installed && ngrok.token_configured ? "acid" : "warning"}>
              {ngrok?.installed && ngrok.token_configured ? "ready" : "setup needed"}
            </Badge>
          </div>
          {!ngrok?.installed && (
            <a className="secondary-button mt-3 inline-flex" href="https://ngrok.com/downloads/windows" target="_blank" rel="noreferrer">
              <ExternalLink className="h-4 w-4" /> Install ngrok
            </a>
          )}
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <div className="relative min-w-0 flex-1">
              <KeyRound className="absolute left-3 top-2.5 h-4 w-4 text-zinc-600" />
              <input
                type="password"
                autoComplete="off"
                className="w-full rounded-md border border-white/[.08] bg-black/30 py-2 pl-9 pr-3 text-xs text-zinc-300 outline-none focus:border-cyan-300/30"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder={ngrok?.token_configured ? "Paste replacement token" : "Paste ngrok authtoken once"}
              />
            </div>
            <button className="secondary-button" disabled={busy || !token.trim()} onClick={() => void saveToken()}>
              <Check className="h-4 w-4" /> {ngrok?.token_configured ? "Replace" : "Save"}
            </button>
            {ngrok?.token_configured && (
              <button className="secondary-button" disabled={busy} onClick={() => void removeToken()}>
                <X className="h-4 w-4" /> Remove
              </button>
            )}
          </div>
          <p className="mt-2 text-[10px] leading-4 text-zinc-600">
            The token is encrypted for this Windows user, never returned by the API, and never placed in a notebook.
          </p>
        </div>
      )}

      <button className="mt-5 flex items-center gap-1 text-[10px] uppercase tracking-widest text-zinc-600" onClick={() => setAdvanced(!advanced)}>
        <ChevronDown className={cn("h-3 w-3 transition-transform", advanced && "rotate-180")} /> Advanced
      </button>
      {advanced && (
        <div className="mt-3 rounded-md border border-white/[.06] bg-black/20 p-3">
          <div className="text-[10px] leading-5 text-zinc-600">
            Local controller: {status?.local_url ?? "checking"}<br />
            Public endpoint: {status?.public_url ?? "not active"}<br />
            Latency: {status?.latency_ms == null ? "—" : String(status.latency_ms) + " ms"}
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input
              className="min-w-0 flex-1 rounded-md border border-white/[.08] bg-black/30 px-3 py-2 font-mono text-xs text-zinc-300 outline-none focus:border-cyan-300/30"
              value={manualUrl}
              onChange={(event) => setManualUrl(event.target.value)}
              placeholder="https://my-aegaeon.example.com"
            />
            <button
              className="secondary-button"
              disabled={busy || !manualUrl.startsWith("https://")}
              onClick={() => void run(() => api.setupRemote({ provider: "manual", public_url: manualUrl }))}
            >
              Verify existing URL
            </button>
          </div>
          <p className="mt-3 text-[10px] leading-4 text-amber-200/70">{status?.development_warning}</p>
        </div>
      )}
    </div>
  );
}

function CheckItem({ label, done, active }: { label: string; done: boolean; active: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-white/[.05] bg-black/20 px-3 py-2 text-[11px] text-zinc-500">
      {done ? (
        <Check className="h-3.5 w-3.5 text-acid" />
      ) : active ? (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan-300" />
      ) : (
        <span className="h-3.5 w-3.5 rounded-full border border-zinc-700" />
      )}
      {label}
    </div>
  );
}
