"use client";

import type { ReactNode } from "react";
import {
  AlertCircle,
  AlertTriangle,
  Check,
  CheckCircle2,
  Circle,
  Clock3,
  Copy,
  Info,
  LoaderCircle,
  RotateCcw,
  X,
} from "lucide-react";
import type { Activity } from "@/lib/types";
import { exactTime, timeAgo } from "@/lib/time";

export { exactTime, timeAgo } from "@/lib/time";

export function cn(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function StatusDot({ status, pulse = false }: { status: string; pulse?: boolean }) {
  const normalized = status.toLowerCase();
  const color =
    normalized === "completed" || normalized === "online" || normalized === "available in demo"
      ? "bg-acid"
      : [
            "running",
            "busy",
            "testing",
            "reviewing",
            "planning",
            "assigned",
            "queued",
            "waiting_for_worker",
          ].includes(normalized)
        ? "bg-cyan"
        : ["failed", "offline", "cancelled", "error"].includes(normalized)
          ? "bg-red-400"
          : "bg-zinc-500";
  return (
    <span className="relative inline-flex h-2 w-2 shrink-0">
      {pulse && <span className={cn("absolute h-full w-full animate-ping rounded-full opacity-50", color)} />}
      <span className={cn("relative h-2 w-2 rounded-full", color)} />
    </span>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={cn("badge", `badge-${tone}`)}>{children}</span>;
}

export function TaskIcon({ status }: { status: string }) {
  switch (status) {
    case "COMPLETED":
      return <Check className="h-3.5 w-3.5 text-acid" strokeWidth={2.5} />;
    case "RUNNING":
    case "ASSIGNED":
      return <LoaderCircle className="h-3.5 w-3.5 animate-spin text-cyan" />;
    case "QUEUED":
    case "WAITING_FOR_WORKER":
      return <Clock3 className="h-3.5 w-3.5 text-cyan" />;
    case "RETRYING":
      return <RotateCcw className="h-3.5 w-3.5 text-amber-300" />;
    case "FAILED":
      return <X className="h-3.5 w-3.5 text-red-400" />;
    case "BLOCKED":
      return <AlertCircle className="h-3.5 w-3.5 text-red-400" />;
    case "READY":
      return <Clock3 className="h-3.5 w-3.5 text-zinc-300" />;
    default:
      return <Circle className="h-3.5 w-3.5 text-zinc-600" />;
  }
}

export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function ActivityFeed({ events, empty = "No activity yet" }: { events: Activity[]; empty?: string }) {
  if (!events.length) return <div className="empty-state py-10">{empty}</div>;
  return <div className="activity-list">{events.map((event) => <ActivityRow event={event} key={event.id} />)}</div>;
}

function ActivityRow({ event }: { event: Activity }) {
  const severity = (event.severity || (event.type.includes("failed") ? "ERROR" : "INFO")).toUpperCase();
  const isError = severity === "ERROR";
  const isContractFailure = Boolean(event.classification?.startsWith("CONTRACT_"));
  const diagnostics = event.diagnostics ?? {};
  const stageHistory = stageEntries(diagnostics.recent_stage_history ?? event.payload.worker_logs);
  const traceback = String(diagnostics.traceback ?? diagnostics.error_repr ?? "");
  const hasDetails = Boolean(
    isError || event.stage || event.classification || event.retry_strategy || Object.keys(diagnostics).length,
  );
  const Icon = isError
    ? AlertCircle
    : severity === "WARNING"
      ? AlertTriangle
      : severity === "SUCCESS"
        ? CheckCircle2
        : Info;
  const errorBlock = [
    `Task: ${event.task_id ?? "—"}`,
    `Job: ${event.job_id ?? "—"}`,
    `Worker: ${event.worker_id ?? "—"}`,
    `Stage: ${event.stage ?? "—"}`,
    `Classification: ${event.classification ?? "—"}`,
    `Error Type: ${event.error_type ?? "—"}`,
    `Message: ${event.message}`,
    `Retry Strategy: ${event.retry_strategy ?? "—"}`,
    `Timestamp: ${event.created_at}`,
  ].join("\n");

  return (
    <div className={cn("activity-row", isError && "activity-row-error", severity === "WARNING" && "activity-row-warning")}>
      <div className={cn("activity-severity-icon", `activity-severity-${severity.toLowerCase()}`)}>
        <Icon className="h-3.5 w-3.5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={isError ? "danger" : severity === "WARNING" ? "warning" : severity === "SUCCESS" ? "acid" : "neutral"}>{severity}</Badge>
              {event.stage && <span className="activity-stage">{event.stage.replaceAll("_", " ")}</span>}
            </div>
            <p className={cn("mt-2 text-[13px]", isError ? "font-medium text-red-200" : "text-zinc-200")}>{event.message}</p>
            {isContractFailure && <div className="mt-3 rounded-lg border border-amber-400/25 bg-amber-400/[.06] p-3"><div className="text-[10px] font-semibold uppercase tracking-[.14em] text-amber-200">Contract change requires review</div><div className="mt-2 grid gap-2 text-[10px] text-zinc-500 sm:grid-cols-2"><span>Worker generation: <strong className="text-acid">{String(diagnostics.worker_generation ?? "SUCCEEDED")}</strong></span><span>Artifact persistence: <strong className="text-acid">{String(diagnostics.artifact_persistence ?? "SUCCEEDED")}</strong></span><span>Path: <strong className="break-all font-mono text-zinc-300">{String(diagnostics.actual_path ?? diagnostics.path ?? "—")}</strong></span><span>Action: <strong className="text-amber-200">{event.retry_strategy ?? "Review proposal"}</strong></span></div></div>}
          </div>
          <div className="shrink-0 text-right text-[10px] text-zinc-600">
            <time dateTime={event.created_at} title={exactTime(event.created_at)}>{timeAgo(event.created_at)}</time>
            {isError && <div className="mt-1 normal-case tracking-normal text-zinc-500">{exactTime(event.created_at)}</div>}
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[9px] uppercase tracking-[.12em] text-zinc-600">
          <span>{event.type.replaceAll(".", " / ")}</span>
          {event.classification && <><span>·</span><span className="text-red-300/70">{event.classification}</span></>}
          {event.attempt != null && <><span>·</span><span>Attempt {event.attempt}</span></>}
        </div>
        {hasDetails && (
          <details className="activity-details">
            <summary>View details</summary>
            <div className="activity-detail-grid">
              <DetailValue label="Task" value={event.task_id} />
              <DetailValue label="Worker" value={event.worker_id} />
              <DetailValue label="Job" value={event.job_id} />
              <DetailValue label="Run" value={event.run_id} />
              <DetailValue label="Stage" value={event.stage} />
              <DetailValue label="Classification" value={event.classification} />
              <DetailValue label="Error type" value={event.error_type} />
              <DetailValue label="Retry" value={event.retry_strategy} />
            </div>
            {stageHistory.length > 0 && (
              <div className="stage-timeline">
                {stageHistory.map((entry, index) => (
                  <div key={`${entry.stage}-${index}`}><Check className="h-3 w-3" /><span>{entry.stage.replaceAll("_", " ")}</span><small>{entry.message}</small></div>
                ))}
                {event.stage && <div className="stage-failed"><X className="h-3 w-3" /><span>{event.stage.replaceAll("_", " ")}</span><small>Failure occurred here</small></div>}
              </div>
            )}
            {traceback && <pre className="diagnostic-traceback">{traceback}</pre>}
            <div className="diagnostic-actions">
              <CopyButton label="Copy Error" value={errorBlock} />
              <CopyButton label="Copy Diagnostics" value={JSON.stringify({ event: errorBlock, diagnostics }, null, 2)} />
              {traceback && <CopyButton label="Copy traceback" value={traceback} />}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}

function stageEntries(value: unknown): Array<{ stage: string; message: string }> {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({ stage: String(item.stage ?? "WORKER"), message: String(item.message ?? "Completed") }))
    .slice(-10);
}

function DetailValue({ label, value }: { label: string; value: string | null }) {
  return <div><span>{label}</span><strong title={value ?? "—"}>{value ?? "—"}</strong></div>;
}

function CopyButton({ label, value }: { label: string; value: string }) {
  return <button type="button" onClick={() => void navigator.clipboard.writeText(value)}><Copy className="h-3 w-3" />{label}</button>;
}

export function MiniBar({ value, tone = "acid" }: { value: number; tone?: "acid" | "cyan" }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-white/[.06]">
      <div className={cn("h-full rounded-full transition-all duration-500", tone === "acid" ? "bg-acid" : "bg-cyan")} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}