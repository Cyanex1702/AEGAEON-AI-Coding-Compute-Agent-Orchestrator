import type {
  Activity,
  DashboardStats,
  Integrations,
  Job,
  ModelEvidence,
  ModelRecommendation,
  NewProjectPayload,
  Notebook,
  Project,
  ProjectFile,
  ProviderStatus,
  RegisteredModel,
  RemoteConnectivity,
  Worker,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_AEGAEON_API_URL ?? "http://127.0.0.1:8000";
export const WS_URL = process.env.NEXT_PUBLIC_AEGAEON_WS_URL ?? "ws://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(data.detail ?? "Request failed");
  }
  return response.json() as Promise<T>;
}

export const api = {
  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  workers: () => request<Worker[]>("/workers"),
  models: () => request<RegisteredModel[]>("/models"),
  stats: () => request<DashboardStats>("/stats/dashboard"),
  events: (projectId?: string) =>
    request<Activity[]>(projectId ? `/projects/${projectId}/events` : "/events"),
  files: (projectId: string) => request<ProjectFile[]>(`/projects/${projectId}/files`),
  jobs: (projectId: string) => request<Job[]>(`/projects/${projectId}/jobs`),
  createProject: (payload: NewProjectPayload) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(payload) }),
  runProject: (id: string) => request(`/projects/${id}/run`, { method: "POST" }),
  cancelProject: (id: string) => request(`/projects/${id}/cancel`, { method: "POST" }),
  providerStatus: () => request<ProviderStatus>("/provider/status"),
  checkProvider: () => request<ProviderStatus>("/provider/check", { method: "POST" }),
integrations: () => request<Integrations>("/integrations"),
  remoteConnectivity: () => request<RemoteConnectivity>("/remote-connectivity"),
  setupRemote: (payload: { provider: "auto" | "ngrok" | "manual"; public_url?: string }) =>
    request<RemoteConnectivity>("/remote-connectivity/setup", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  restartRemote: () =>
    request<RemoteConnectivity>("/remote-connectivity/restart", { method: "POST" }),
  stopRemote: () =>
    request<RemoteConnectivity>("/remote-connectivity/stop", { method: "POST" }),
  saveRemoteToken: (provider: string, token: string) =>
    request<{ configured: boolean }>(`/remote-connectivity/providers/${provider}/token`, {
      method: "PUT",
      body: JSON.stringify({ token }),
    }),
  removeRemoteToken: (provider: string) =>
    request<{ configured: boolean }>(`/remote-connectivity/providers/${provider}/token`, {
      method: "DELETE",
    }),
  scoutModels: () => request<ModelEvidence[]>("/model-scout/models"),
  recommendModels: (project: string, vramMb: number, quantization: string) =>
    request<ModelRecommendation[]>("/model-scout/recommend", {
      method: "POST",
      body: JSON.stringify({
        project,
        role: "general coding",
        hardware: { vram_mb: vramMb, quantization },
        limit: 3,
      }),
    }),
  generateNotebooks: (
    projectId: string,
    payload: { controller_url?: string; worker_count?: number; model_id?: string; quantization?: string },
  ) =>
    request<Notebook[]>(`/projects/${projectId}/workers/notebooks`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

export const projectExportUrl = (projectId: string) => `${API_URL}/projects/${projectId}/export`;

