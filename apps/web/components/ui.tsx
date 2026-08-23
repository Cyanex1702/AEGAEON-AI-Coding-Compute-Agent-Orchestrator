"use client";

import type { ReactNode } from "react";
import {
  AlertCircle,
  Check,
  Circle,
  Clock3,
  LoaderCircle,
  RotateCcw,
  X,
} from "lucide-react";
import type { Activity } from "@/lib/types";

export function cn(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function StatusDot({ status, pulse = false }: { status: string; pulse?: boolean }) {
  const normalized = status.toLowerCase();
  const color =
    normalized === "completed" || normalized === "online" || normalized === "available in demo"
      ? "bg-acid"
      : ["running", "busy", "testing", "reviewing", "planning", "assigned"].includes(normalized)
        ? "bg-cyan"
        : ["failed", "offline", "cancelled"].includes(normalized)
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

export function timeAgo(value: string | null | undefined) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 5) return "now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function ActivityFeed({ events, empty = "No activity yet" }: { events: Activity[]; empty?: string }) {
  if (!events.length) {
    return <div className="empty-state py-10">{empty}</div>;
  }
  return (
    <div className="activity-list">
      {events.map((event) => (
        <div className="activity-row" key={event.id}>
          <div className="activity-rail">
            <StatusDot status={event.type.includes("failed") ? "failed" : "online"} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] text-zinc-200">{event.message}</p>
            <div className="mt-1 flex items-center gap-2 text-[10px] uppercase tracking-[.12em] text-zinc-600">
              <span>{event.type.replaceAll(".", " / ")}</span>
              <span>·</span>
              <span>{timeAgo(event.created_at)}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function MiniBar({ value, tone = "acid" }: { value: number; tone?: "acid" | "cyan" }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-white/[.06]">
      <div
        className={cn("h-full rounded-full transition-all duration-500", tone === "acid" ? "bg-acid" : "bg-cyan")}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

