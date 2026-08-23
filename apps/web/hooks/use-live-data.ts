"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, WS_URL } from "@/lib/api";
import type { Activity, DashboardStats, Project, RegisteredModel, Worker } from "@/lib/types";

const emptyStats: DashboardStats = {
  projects: 0,
  active_projects: 0,
  online_workers: 0,
  gpu_workers: 0,
  running_tasks: 0,
};

export function useLiveData() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [models, setModels] = useState<RegisteredModel[]>([]);
  const [events, setEvents] = useState<Activity[]>([]);
  const [stats, setStats] = useState<DashboardStats>(emptyStats);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [projectData, workerData, modelData, statsData] = await Promise.all([
        api.projects(),
        api.workers(),
        api.models(),
        api.stats(),
      ]);
      setProjects(projectData);
      setWorkers(workerData);
      setModels(modelData);
      setStats(statsData);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Controller unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const socket = new WebSocket(`${WS_URL}/ws/ui`);
    socket.onopen = () => setConnected(true);
    socket.onclose = () => setConnected(false);
    socket.onerror = () => setConnected(false);
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as Activity | { type: string; message: string };
      if ("id" in event) setEvents((current) => [event, ...current].slice(0, 80));
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => void refresh(), 180);
    };
    const poll = setInterval(() => void refresh(), 10000);
    return () => {
      clearInterval(poll);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      socket.close();
    };
  }, [refresh]);

  return {
    projects,
    workers,
    models,
    events,
    stats,
    connected,
    loading,
    error,
    refresh,
  };
}

