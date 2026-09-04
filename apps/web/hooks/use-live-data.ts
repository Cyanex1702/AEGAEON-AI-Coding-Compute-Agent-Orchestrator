"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, WS_URL } from "@/lib/api";
import type {
  Activity,
  ComputeOverview,
  DashboardStats,
  Project,
  RegisteredModel,
  Worker,
} from "@/lib/types";

const emptyStats: DashboardStats = {
  projects: 0,
  active_projects: 0,
  online_workers: 0,
  gpu_workers: 0,
  running_tasks: 0,
};

function mergeEvents(current: Activity[], incoming: Activity[]) {
  return Array.from(new Map([...current, ...incoming].map((item) => [item.id, item])).values())
    .sort((left, right) => +new Date(right.created_at) - +new Date(left.created_at))
    .slice(0, 80);
}

export function useLiveData() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [models, setModels] = useState<RegisteredModel[]>([]);
  const [events, setEvents] = useState<Activity[]>([]);
  const [stats, setStats] = useState<DashboardStats>(emptyStats);
  const [compute, setCompute] = useState<ComputeOverview | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshing = useRef<Promise<void> | null>(null);
  const lastEventId = useRef<string | null>(null);

  const refresh = useCallback(() => {
    if (refreshing.current) return refreshing.current;
    const pending = (async () => {
      const results = await Promise.allSettled([
        api.projects().then(setProjects),
        api.workers().then(setWorkers),
        api.models().then(setModels),
        api.stats().then(setStats),
        api.events().then((items) => {
          if (items[0]?.id) lastEventId.current = items[0].id;
          setEvents((current) => mergeEvents(current, items));
        }),
        api.compute().then(setCompute),
      ]);
      const failures = results.filter(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      if (failures.length === results.length) {
        const first = failures[0]?.reason;
        setError(first instanceof Error ? first.message : "Controller unavailable");
      } else {
        setError(
          failures.length > 0
            ? failures.length +
                " dashboard source" +
                (failures.length === 1 ? "" : "s") +
                " unavailable"
            : null,
        );
      }
      setLoading(false);
    })();
    refreshing.current = pending;
    void pending.finally(() => {
      if (refreshing.current === pending) refreshing.current = null;
    });
    return pending;
  }, []);

  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;

    const scheduleRefresh = () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => void refresh(), 350);
    };

    const connectSocket = () => {
      if (disposed || !navigator.onLine) return;
      const cursor = lastEventId.current
        ? "?after=" + encodeURIComponent(lastEventId.current)
        : "";
      socket = new WebSocket(WS_URL + "/ws/ui" + cursor);
      socket.onopen = () => {
        reconnectAttempt = 0;
        setConnected(true);
      };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as Activity | { type: string; message: string };
          if ("id" in event) {
            lastEventId.current = event.id;
            setEvents((current) => mergeEvents(current, [event]));
          }
          scheduleRefresh();
        } catch {
          setError("Controller sent an invalid live event");
        }
      };
      socket.onerror = () => setConnected(false);
      socket.onclose = () => {
        setConnected(false);
        if (disposed) return;
        const ceiling = Math.min(30_000, 1_000 * 2 ** reconnectAttempt);
        reconnectAttempt = Math.min(reconnectAttempt + 1, 6);
        reconnectTimer = setTimeout(connectSocket, Math.max(500, Math.random() * ceiling));
      };
    };

    const initialRefresh = window.setTimeout(() => void refresh(), 0);
    const poll = window.setInterval(() => void refresh(), 15_000);
    const online = () => {
      if (!socket || socket.readyState === WebSocket.CLOSED) connectSocket();
      void refresh();
    };
    const offline = () => setConnected(false);
    window.addEventListener("online", online);
    window.addEventListener("offline", offline);
    connectSocket();

    return () => {
      disposed = true;
      window.clearTimeout(initialRefresh);
      window.clearInterval(poll);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      window.removeEventListener("online", online);
      window.removeEventListener("offline", offline);
      socket?.close();
    };
  }, [refresh]);

  return {
    projects,
    workers,
    models,
    events,
    stats,
    compute,
    connected,
    loading,
    error,
    refresh,
  };
}
