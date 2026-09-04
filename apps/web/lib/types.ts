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
  run_id: string | null;
  attempt_id: string | null;
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
  diagnostics: Record<string, unknown>;
  failure_stage: string | null;
  failure_classification: string | null;
  error_type: string | null;
  retry_strategy: string | null;
  failed_at: string | null;
  logs: Array<{ stage?: string; level?: string; message?: string; telemetry?: Record<string, unknown> }>;
  lease_expires_at: string | null;
  assigned_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Run = {
  id: string;
  project_id: string;
  status: string;
  recovered: boolean;
  summary: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
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
  active_run_id: string | null;
  is_pinned: boolean;
  sort_order: number;
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
    ram_available_mb?: number;
    disk_free_mb?: number;
    gpu?: {
      available?: boolean;
      name?: string | null;
      vram_mb?: number;
      allocated_mb?: number;
      reserved_mb?: number;
      free_mb?: number;
    };
    gpus?: ComputeGpu[];
    memory?: {
      ram_available_mb?: number;
      vram_allocated_mb?: number;
      vram_reserved_mb?: number;
      vram_free_mb?: number;
    };
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
    memory_footprint_mb?: number;
    max_recommended_prompt_tokens?: number;
    max_recommended_output_tokens?: number;
    memory_risk_profile?: string;
    supports_cpu_offload?: boolean;
    supports_multi_gpu?: boolean;
  }[];
  cpu_percent: number;
  ram_percent: number;
  gpu_percent: number;
  vram_used_mb: number;
  vram_allocated_mb?: number;
  vram_reserved_mb?: number;
  vram_free_mb?: number;
  ram_available_mb?: number;
  current_task: string | null;
  current_job_id: string | null;
  stage: string | null;
  progress_percent: number;
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
  run_id: string | null;
  job_id: string | null;
  project_id: string | null;
  task_id: string | null;
  worker_id: string | null;
  message: string;
  severity: "SUCCESS" | "INFO" | "RUNNING" | "WARNING" | "ERROR" | string;
  stage: string | null;
  classification: string | null;
  error_type: string | null;
  retry_strategy: string | null;
  attempt: number | null;
  diagnostics: Record<string, unknown>;
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
  compute_strategy: "cheapest" | "balanced" | "fast" | "maximum_quality";
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



export type OrchestrationAnalysis = {
  complexity: "LOW" | "MEDIUM" | "HIGH";
  required_features: string[];
  platform_targets: string[];
  data_requirements: string[];
  security_requirements: string[];
  unknowns: string[];
};

export type Milestone = {
  id: string;
  key: string;
  title: string;
  goal: string;
  description: string;
  sequence: number;
  dependencies: string[];
  deliverables: string[];
  acceptance_criteria: string[];
  status: string;
  base_commit: string | null;
  completion_commit: string | null;
  starting_contract_version: number;
  ending_contract_version: number | null;
};

export type ProjectRequirement = {
  id: string;
  text: string;
  category: string;
  priority: string;
  milestone_id: string | null;
  status: string;
  evidence: string[];
};

export type ProjectOrchestration = {
  analysis: OrchestrationAnalysis | null;
  blueprint: {
    architecture: Record<string, string>;
    entities: Array<{ name: string; identifier?: string }>;
    services: Array<{ name: string }>;
    constraints: string[];
    risks: string[];
  } | null;
  contract: {
    id: string;
    version: number;
    content: {
      architecture?: Record<string, string>;
      entities?: Record<string, unknown>;
      services?: Record<string, unknown>;
      api?: Record<string, unknown>;
      shared_types?: Record<string, unknown>;
      naming?: Record<string, unknown>;
    };
  } | null;
  compute_plan: {
    strategy: "cheapest" | "balanced" | "fast" | "maximum_quality";
    recommended_worker_count: number;
    selected_worker_count: number;
    milestone_count: number;
    estimated_task_count: number;
    parallelizable_task_count: number;
    recommended_roles: string[];
  } | null;
  milestones: Milestone[];
  milestone_tasks: Record<string, Task[]>;
  requirements: ProjectRequirement[];
  architecture_decisions: Array<{
    id: string; topic: string; decision: string; reason: string; contract_version: number;
  }>;
  lead_decisions: Array<{
    id: string; decision_type: string; summary: string; rationale: string; evidence: string[];
  }>;
  contract_proposals: Array<{
    id: string;
    task_id: string | null;
    worker_id: string | null;
    proposal_type: string;
    target: string;
    change: Record<string, unknown>;
    current_value: unknown;
    proposed_value: unknown;
    expected_revision: number | null;
    reason: string;
    status: string;
    review_reason: string;
    created_at: string;
    reviewed_at: string | null;
  }>;
  contract_revisions: Array<{
    id: string;
    version: number;
    previous_version: number | null;
    reason: string;
    proposal_id: string | null;
    affected_tasks: string[];
  }>;
  verifications: Array<{
    id: string; scope: string; status: string; evidence: string[]; commit: string | null;
  }>;
};

export type ComputeGpu = {
  index: number;
  name: string;
  total_mb: number;
  allocated_mb: number;
  reserved_mb: number;
  free_mb: number;
  utilization_percent: number;
  compute_capability: string | null;
};

export type ComputeWorker = {
  worker_id: string;
  hostname: string;
  status: string;
  gpus: ComputeGpu[];
  ram_total_mb: number;
  ram_available_mb: number;
  cpu_cores: number;
  disk_free_mb: number;
  current_running_jobs: number;
  resident_model: string | null;
  runtime: string | null;
  quantization: string | null;
  model_memory_mb: number;
  max_recommended_prompt_tokens: number;
  max_recommended_output_tokens: number;
  memory_risk_profile: string;
  supports_cpu_offload: boolean;
  supports_multi_gpu: boolean;
};

export type ComputePolicy = {
  preset: "QUALITY_FIRST" | "BALANCED" | "MEMORY_SAFE" | "FAST";
  allow_model_fallback: boolean;
  minimum_quality_score: number;
  prefer_same_model: boolean;
  prefer_same_model_family: boolean;
  allow_runtime_change: boolean;
  allow_quantization_change: boolean;
  allow_cpu_offload: boolean;
  allow_multi_gpu: boolean;
  allow_cpu_only: boolean;
  minimum_viable_output_tokens: number;
  ram_safety_buffer_mb: number;
};

export type ContextPlan = {
  token_budget: number;
  estimated_tokens: number;
  packed_tokens: number;
  dropped_sections: string[];
};

export type ModelExecutionPlan = {
  id: string;
  project_id: string | null;
  task_id: string | null;
  model_id: string;
  runtime: string;
  quantization: string;
  worker_id: string | null;
  device_strategy: string;
  prompt_token_budget: number;
  maximum_new_tokens: number;
  memory_strategy: string;
  offload_strategy: string;
  estimated_vram_required_mb: number;
  estimated_ram_required_mb: number;
  estimated_oom_risk: string;
  fit_score: number;
  fallback_models: string[];
  fallback_workers: string[];
  task_split_allowed: boolean;
  recovery_policy: string[];
  next_recovery_action: string | null;
  reasoning_summary: string[];
  context_plan: ContextPlan | null;
  created_at: string;
};

export type ComputeObservation = {
  id: string;
  project_id: string | null;
  task_id: string | null;
  worker_id: string | null;
  model_id: string;
  runtime: string;
  quantization: string;
  gpu_name: string;
  vram_total_mb: number;
  prompt_tokens: number;
  output_limit: number;
  generated_tokens: number;
  peak_vram_mb: number;
  elapsed_seconds: number;
  result: string;
  failure_classification: string | null;
  created_at: string;
};

export type ComputeIncident = {
  id: string;
  root_failure_id: string;
  project_id: string;
  task_id: string;
  worker_id: string | null;
  model_id: string | null;
  gpu_name: string | null;
  classification: string;
  state: "RECOVERING" | "RETRYING" | "RESOLVED" | "FAILED_FINAL";
  attempts: Array<Record<string, unknown>>;
  recovery_action: string | null;
  message: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
};

export type ComputeOverview = {
  policy: ComputePolicy;
  workers: ComputeWorker[];
  active_plans: ModelExecutionPlan[];
  observations: ComputeObservation[];
  incidents: ComputeIncident[];
  summary: {
    online_workers: number;
    gpu_count: number;
    total_vram_mb: number;
    free_vram_mb: number;
    active_incidents: number;
    historical_ooms: number;
  };
};
