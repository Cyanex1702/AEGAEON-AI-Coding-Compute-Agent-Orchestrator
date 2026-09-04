import type {
  Activity,
  ComputeOverview,
  ComputePolicy,
  DashboardStats,
  Integrations,
  Job,
  ModelEvidence,
  ModelRecommendation,
  NewProjectPayload,
  Notebook,
  Project,
  ProjectFile,
  ProjectOrchestration,
  ProviderStatus,
  RegisteredModel,
  RemoteConnectivity,
  Run,
  Worker,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_AEGAEON_API_URL ?? "/api/controller";
export const WS_URL = process.env.NEXT_PUBLIC_AEGAEON_WS_URL ?? "ws://127.0.0.1:8000";

const RETRY_DELAY_MS = 300;
const REQUEST_TIMEOUT_MS = 10_000;

function wait(milliseconds: number) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method?.toUpperCase() ?? "GET";
  const attempts = method === "GET" ? 3 : 1;
  let networkFailure: unknown;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let response: Response;
    try {
      response = await fetch(`${API_URL}${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", ...init?.headers },
        cache: "no-store",
        signal: init?.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
    } catch (reason) {
      networkFailure = reason;
      if (attempt + 1 < attempts) {
        await wait(RETRY_DELAY_MS * 2 ** attempt * (0.5 + Math.random()));
        continue;
      }
      break;
    }

    if (response.ok) return response.json() as Promise<T>;

    if (response.status >= 500 && attempt + 1 < attempts) {
      await wait(RETRY_DELAY_MS * 2 ** attempt * (0.5 + Math.random()));
      continue;
    }

    const data = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(
      data.detail ??
        (response.status >= 500
          ? "AEGAEON controller is temporarily unavailable"
          : "Request failed"),
    );
  }

  const detail = networkFailure instanceof Error ? ` (${networkFailure.message})` : "";
  throw new Error(`AEGAEON controller is temporarily unavailable${detail}`);
}

export const api = {
  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  workers: () => request<Worker[]>("/workers"),
  models: () => request<RegisteredModel[]>("/models"),
  compute: (projectId?: string) =>
    request<ComputeOverview>(projectId ? `/compute?project_id=${projectId}` : "/compute"),
  saveComputePolicy: (projectId: string, policy: ComputePolicy) =>
    request<ComputePolicy>(`/projects/${projectId}/compute-policy`, {
      method: "PUT",
      body: JSON.stringify(policy),
    }),
  stats: () => request<DashboardStats>("/stats/dashboard"),
  events: (projectId?: string) =>
    request<Activity[]>(projectId ? `/projects/${projectId}/events` : "/events"),
  files: (projectId: string) => request<ProjectFile[]>(`/projects/${projectId}/files`),
  jobs: (projectId: string) => request<Job[]>(`/projects/${projectId}/jobs`),
  runs: (projectId: string) => request<Run[]>(`/projects/${projectId}/runs`),
  orchestration: (projectId: string) =>
    request<ProjectOrchestration>(`/projects/${projectId}/orchestration`),
  updateMilestone: (
    projectId: string,
    milestoneId: string,
    values: Record<string, unknown>,
  ) =>
    request<ProjectOrchestration>(
      `/projects/${projectId}/milestones/${milestoneId}`,
      { method: "PATCH", body: JSON.stringify(values) },
    ),
  updateComputePlan: (
    projectId: string,
    strategy: string,
    workerCount: number,
  ) =>
    request<ProjectOrchestration>(`/projects/${projectId}/compute-plan`, {
      method: "PUT",
      body: JSON.stringify({ strategy, worker_count: workerCount }),
    }),
  createProject: (payload: NewProjectPayload) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(payload) }),
  pinProject: (id: string, isPinned: boolean) =>
    request<Project>(`/projects/${id}/pin`, {
      method: "PUT",
      body: JSON.stringify({ is_pinned: isPinned }),
    }),
  moveProject: (id: string, direction: "up" | "down") =>
    request<Project>(`/projects/${id}/move`, {
      method: "POST",
      body: JSON.stringify({ direction }),
    }),
  deleteProject: (id: string) =>
    request<{ project_id: string; filesystem_removed: boolean }>(`/projects/${id}`, {
      method: "DELETE",
    }),
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




