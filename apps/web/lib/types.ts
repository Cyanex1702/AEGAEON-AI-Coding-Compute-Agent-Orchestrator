export type Task = {
  id: string;
  project_id: string;
  key: string;
  title: string;
  description: string;
  agent_role: string;
  status: string;
  dependencies: string[];
  required_capabilities: string[];
  sequence: number;
  retry_count: number;
  max_retries: number;
  worker_id: string | null;
  job_id: string | null;
  duration_seconds: number | null;
  logs: string[];
  files_modified: string[];
  result: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
};

export type Job = {
  id: string;
  project_id: string;
  task_id: string;
  job_type: string;
  agent_role: string;
  worker_id: string | null;
  status: string;
  attempt: number;
  requirements: Record<string, unknown>;
  instructions: string;
  payload_summary: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
  lease_expires_at: string | null;
  assigned_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Project = {
  id: string;
  name: string;
  prompt: string;
  status: string;
  strategy: string;
  progress: number;
  workspace_path: string;
  options: Record<string, unknown>;
  summary: string;
  created_at: string;
  updated_at: string;
  tasks: Task[];
};

export type Worker = {
  id: string;
  hostname: string;
  status: string;
  hardware: {
    cpu_cores?: number;
    ram_mb?: number;
    gpu?: { available?: boolean; name?: string | null; vram_mb?: number };
  };
  capabilities: string[];
  models: {
    id: string;
    provider: string;
    loaded: boolean;
    runtime?: string | null;
    quantization?: string | null;
    status?: string;
    context_length?: number;
    task_scores?: Record<string, number>;
    last_error?: string | null;
  }[];
  cpu_percent: number;
  ram_percent: number;
  gpu_percent: number;
  vram_used_mb: number;
  current_task: string | null;
  last_heartbeat: string;
};

export type RegisteredModel = {
  id: string;
  name: string;
  type: string;
  provider: string;
  capabilities: string[];
  minimum_vram_mb: number;
  context_length: number;
  status: string;
};

export type Activity = {
  id: string;
  type: string;
  project_id: string | null;
  task_id: string | null;
  worker_id: string | null;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type ProjectFile = { path: string; size: number };

export type ProviderStatus = {
  provider: string;
  model: string;
  base_url: string;
  mode: "demo" | "model";
  configured: boolean;
  reachable: boolean | null;
  structured_output_mode: string;
  error: string | null;
};

export type DashboardStats = {
  projects: number;
  active_projects: number;
  online_workers: number;
  gpu_workers: number;
  running_tasks: number;
};

export type NewProjectPayload = {
  name?: string;
  prompt: string;
  strategy: string;
  intelligence: number;
  maximum_retries: number;
  run_tests: boolean;
  review_code: boolean;
  retry_failed_tasks: boolean;
  execution_mode: "rich_boy" | "broke_boy";
  worker_count: number;
  compute_target: "google_colab" | "kaggle" | "local_gpu" | "cloud_gpu";
  model_selection: "recommend" | "manual";
  selected_model?: string;
  target_vram_mb: number;
  quantization: "4bit" | "8bit" | "bf16";
  research_level: "standard" | "manual" | "ai_assisted" | "multi_advisor";
};

export type ModelEvidence = {
  id: string;
  publisher: string;
  family: string;
  parameter_count_b: number | null;
  instruction_tuned: boolean;
  context_length: number | null;
  runtimes: string[];
  quantizations: string[];
  license: string | null;
  gated: boolean;
  safe_vram_mb: Record<string, number>;
  benchmark_scores: Record<string, number>;
  source_url: string;
};

export type ModelRecommendation = {
  model: ModelEvidence;
  score: number;
  confidence: string;
  score_breakdown: Record<string, number>;
  reasons: string[];
  warnings: string[];
  recommended_quantization: string;
  estimated_vram_mb: number;
};

export type Notebook = {
  artifact_id: string;
  filename: string;
  worker_name: string;
  model_id: string;
  pairing_code: string;
  pairing_expires_at: string;
  download_url: string;
  controller_url: string;
  connection_generation: number;
  stale: boolean;
};

export type Integrations = {
  rich_boy_provider: ProviderStatus;
  hugging_face: { configured: boolean; secret_exposed: boolean };
  manual_advisors: { supported: boolean; connected: boolean; workflow: string };
};


export type TunnelProvider = {
  id: string;
  name: string;
  installed: boolean;
  token_required: boolean;
  token_configured: boolean;
  automatic: boolean;
  setup_url: string | null;
  detail: string;
};

export type RemoteConnectivity = {
  state: "not_configured" | "checking" | "starting_tunnel" | "waiting_provider" |
    "testing_https" | "testing_websocket" | "ready" | "reconnecting" | "failed" | "stopped";
  provider: string | null;
  local_url: string;
  local_controller: boolean;
  public_url: string | null;
  rest_ok: boolean;
  websocket_ok: boolean;
  latency_ms: number | null;
  error: string | null;
  generation: number;
  url_changed: boolean;
  stale_notebooks: number;
  reconnect_attempt: number;
  development_warning: string;
  providers: TunnelProvider[];
  updated_at: string;
};