# ruff: noqa: E501
from __future__ import annotations

import json
from dataclasses import dataclass

from aegaeon.artifacts.manager import ArtifactManager
from aegaeon.models.structured_output import PORTABLE_MODEL_RESPONSE_PARSER_SOURCE
from aegaeon.protocol.schemas import NotebookRead
from aegaeon.workers.pairing import WorkerPairingService


@dataclass(slots=True)
class NotebookSpec:
    project_id: str
    worker_name: str
    model_id: str
    quantization: str
    controller_url: str
    minimum_vram_mb: int
    model_candidates: tuple[dict[str, int | str], ...] = ()
    connection_generation: int = 0


class ColabNotebookGenerator:
    """Deterministic Run-All notebook template for one complete model per worker."""

    def __init__(self, artifacts: ArtifactManager, pairing: WorkerPairingService) -> None:
        self.artifacts = artifacts
        self.pairing = pairing

    def generate(self, spec: NotebookSpec) -> NotebookRead:
        code, expires = self.pairing.create(spec.project_id, spec.worker_name, spec.model_id)
        artifact = self.artifacts.create(
            spec.project_id,
            None,
            "worker_notebook",
            f"{spec.worker_name}.ipynb",
            json.dumps(self._notebook(spec), indent=2),
        )
        return NotebookRead(
            artifact_id=artifact.id,
            filename=artifact.filename,
            worker_name=spec.worker_name,
            model_id=spec.model_id,
            pairing_code=code,
            pairing_expires_at=expires,
            download_url=f"/artifacts/{artifact.id}",
            controller_url=spec.controller_url,
            connection_generation=spec.connection_generation,
            stale=False,
        )

    @staticmethod
    def _notebook(spec: NotebookSpec) -> dict[str, object]:
        model_candidates = spec.model_candidates or (
            {
                "id": spec.model_id,
                "base_vram_mb": spec.minimum_vram_mb,
                "estimated_peak_vram_mb": spec.minimum_vram_mb,
            },
        )
        install = """import subprocess, sys
subprocess.check_call([
    sys.executable, "-m", "pip", "install", "-q",
    "transformers>=4.46", "accelerate>=1", "bitsandbytes>=0.44",
    "websockets>=14,<16", "psutil", "nest_asyncio",
])"""
        setup = """import getpass, json, urllib.error, urllib.request

CONTROLLER_HTTP = "__CONTROLLER__".rstrip("/")
REQUESTED_MODEL_ID = "__MODEL__"
MODEL_ID = REQUESTED_MODEL_ID
WORKER_NAME = "__WORKER__"
QUANTIZATION = "__QUANTIZATION__"
MINIMUM_VRAM_MB = __MINIMUM_VRAM__
MODEL_CANDIDATES = __MODEL_CANDIDATES__
PAIRING_CODE = getpass.getpass("Enter the temporary AEGAEON pairing code: ")
request = urllib.request.Request(
    CONTROLLER_HTTP + "/pairing/redeem",
    data=json.dumps({"pairing_code": PAIRING_CODE}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        credential = json.load(response)
except urllib.error.HTTPError as error:
    raise RuntimeError(
        "Pairing failed. The one-time code may be expired or already used.\\n"
        "Return to AEGAEON and choose Generate fresh workers."
    ) from error
except Exception as error:
    raise RuntimeError(
        f"AEGAEON could not reach {CONTROLLER_HTTP}.\\n"
        "Return to AEGAEON and select Reconnect Remote Workers."
    ) from error
WORKER_TOKEN = credential["worker_token"]
WORKER_ID = credential["worker_id"]
print("Paired. The temporary credential stays only in this notebook session.")
"""
        setup = (
            setup.replace("__CONTROLLER__", spec.controller_url)
            .replace("__MODEL__", spec.model_id)
            .replace("__WORKER__", spec.worker_name)
            .replace("__QUANTIZATION__", spec.quantization)
            .replace("__MINIMUM_VRAM__", str(spec.minimum_vram_mb))
            .replace("__MODEL_CANDIDATES__", json.dumps(model_candidates))
        )
        load = """import os, shutil, subprocess
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

if not shutil.which("nvidia-smi"):
    raise RuntimeError("No NVIDIA GPU detected. Choose a GPU runtime and run again.")
query = subprocess.run(
    ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,compute_cap", "--format=csv,noheader,nounits"],
    check=True, capture_output=True, text=True,
).stdout.strip().splitlines()[0]
gpu_name, actual_vram, initial_free_vram, compute_capability = [part.strip() for part in query.split(",")]
actual_vram = int(actual_vram)
initial_free_vram = int(initial_free_vram)
if actual_vram < MINIMUM_VRAM_MB:
    raise RuntimeError(
        f"This model needs about {MINIMUM_VRAM_MB} MB VRAM; this runtime has {actual_vram} MB."
    )
available_budget_mb = int(min(actual_vram, initial_free_vram) * 0.80)
eligible_candidates = [
    item for item in MODEL_CANDIDATES
    if int(item["estimated_peak_vram_mb"]) <= available_budget_mb
]
if not eligible_candidates:
    details = ", ".join(
        f"{item['id']} needs about {item['estimated_peak_vram_mb']} MB peak"
        for item in MODEL_CANDIDATES
    )
    raise RuntimeError(
        f"No trusted model retains AEGAEON's 20% VRAM reserve on {gpu_name}. "
        f"Safe budget: {available_budget_mb} MB. Candidates: {details}"
    )
MODEL_ID = str(eligible_candidates[0]["id"])
if MODEL_ID != REQUESTED_MODEL_ID:
    print(f"Resource governor selected {MODEL_ID} instead of unsafe {REQUESTED_MODEL_ID}.")
print(f"Detected {gpu_name} with {actual_vram} MB VRAM")
hf_token = getpass.getpass("Hugging Face token (blank for public models): ") or None
quantization_config = None
if QUANTIZATION == "4bit":
    quantization_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16)
elif QUANTIZATION == "8bit":
    quantization_config = BitsAndBytesConfig(load_in_8bit=True)
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=hf_token)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID, token=hf_token, device_map="auto",
    quantization_config=quantization_config, torch_dtype="auto",
)
model_memory_mb = int(torch.cuda.memory_allocated() / 1024 / 1024)
input_device = model.get_input_embeddings().weight.device
probe_token = tokenizer.eos_token_id or tokenizer.pad_token_id or 0
safe_prompt_tokens = 0
smoke_peak_mb = model_memory_mb
probe_failure = None
for probe_tokens in (1_024, 2_048, 4_096, 6_144):
    probe_ids = probe_mask = probe_output = None
    try:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        probe_ids = torch.full((1, probe_tokens), probe_token, dtype=torch.long, device=input_device)
        probe_mask = torch.ones_like(probe_ids)
        probe_output = model.generate(
            input_ids=probe_ids,
            attention_mask=probe_mask,
            max_new_tokens=1,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
        peak_mb = int(torch.cuda.max_memory_allocated() / 1024 / 1024)
        smoke_peak_mb = max(smoke_peak_mb, peak_mb)
        if peak_mb > int(actual_vram * 0.80):
            break
        safe_prompt_tokens = probe_tokens
    except torch.OutOfMemoryError as error:
        probe_failure = error
        break
    finally:
        del probe_ids, probe_mask, probe_output
        torch.cuda.empty_cache()
if safe_prompt_tokens == 0:
    raise RuntimeError(f"MODEL_PREFLIGHT_CUDA_OOM: {probe_failure or 'insufficient headroom'}")
max_recommended_prompt_tokens = safe_prompt_tokens
max_recommended_output_tokens = (
    1_536 if safe_prompt_tokens <= 2_048
    else 2_048 if safe_prompt_tokens <= 4_096
    else 3_072
)
headroom_ratio = max(0, actual_vram - smoke_peak_mb) / max(1, actual_vram)
memory_risk_profile = (
    "LOW"
    if headroom_ratio >= 0.30
    else "MODERATE"
    if headroom_ratio >= 0.20
    else "HIGH"
)
print("Model loaded. AEGAEON is ready to use this GPU.")
"""
        worker = '''import asyncio, gc, hashlib, json, os, pathlib, random, re, secrets, time, traceback
import nest_asyncio, psutil, websockets
from websockets.exceptions import ConnectionClosed

SYSTEM = """You are an AEGAEON bounded coding worker. Return raw source files using only
AEGAEON_RESPONSE_V1. Never return JSON or Markdown. Use this exact shape:
AEGAEON_RESPONSE_V1
SUMMARY: one short line
<<<FILE:relative/path>>>
raw file content; quotes and newlines need no escaping
<<<END_FILE>>>
<<<END_RESPONSE>>>
Repeat the FILE block for each file. Prefer a small complete result over many files.
Do not add a preface, explanation, Markdown fence, or text after <<<END_RESPONSE>>>.
Stop immediately after the final marker. Every response must contain at least one complete file.
Canonical contract files are Core-owned and read-only. If the contract is insufficient,
mention the limitation in SUMMARY and do not modify a protected file directly.
Paths must be repository-relative. Never use hidden control paths, .git, absolute paths, or .. ."""
STATE = {
    "status":"ready", "job_id":None, "attempt_id":None, "stage":"ready",
    "progress":0.0, "sequence":0, "connection_generation":0,
}
SESSION_ID = secrets.token_urlsafe(18)
CURRENT_SOCKET = None
ACTIVE_JOB = None
ACTIVE_TASK = None
OUTBOX = {}
SEND_LOCK = asyncio.Lock()
STAGE_PROGRESS = {
    "assigned":2, "PROMPT_PREPARATION":8, "TOKENIZATION":15, "GENERATION":30,
    "DECODING":70, "PARSING":78, "VALIDATION":86, "UPLOAD":95,
}

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
def portable_artifact_filename(identifier):
    invalid_chars = '<>:"/\\\\|?*'
    candidate = "".join(
        "_" if char in invalid_chars or ord(char) < 32 else char
        for char in str(identifier or "")
    )
    candidate = re.sub(r"_+", "_", candidate).strip(" ._") or "artifact"
    if candidate.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        candidate = "_" + candidate
    return candidate[:175].rstrip(" .") + ".json"
def gpu_snapshot():
    try:
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        return {
            "available": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "total_mb": int(total_bytes/1024/1024),
            "free_mb": int(free_bytes/1024/1024),
            "allocated_mb": int(torch.cuda.memory_allocated()/1024/1024),
            "reserved_mb": int(torch.cuda.memory_reserved()/1024/1024),
            "peak_allocated_mb": int(torch.cuda.max_memory_allocated()/1024/1024),
            "utilization_percent": 0,
        }
    except Exception as error:
        return {"available":False, "telemetry_error":repr(error)}

__MODEL_RESPONSE_PARSER__

def classify_failure(error, stage):
    detail = (str(error) or repr(error)).lower()
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return "DEPENDENCY_FAILURE"
    if "prompt_memory_limit" in detail:
        return "CONTEXT_TOO_LARGE"
    if "out of memory" in detail or "cuda oom" in detail:
        if stage in {"TOKENIZATION", "GENERATION", "DECODING"}:
            return "GENERATION_CUDA_OOM"
        return "MODEL_LOAD_CUDA_OOM"
    if "tokenizer" in detail or stage == "TOKENIZATION":
        return "TOKENIZER_ERROR"
    if "output truncated" in detail:
        return "OUTPUT_TRUNCATED"
    if "exceeds the controller limit" in detail or "too large" in detail:
        return "OUTPUT_TOO_LARGE"
    if "unsafe generated path" in detail:
        return "UNSAFE_PATH"
    if "invalid json" in detail:
        return "INVALID_JSON"
    if "invalid model response" in detail or stage in {"PARSING", "VALIDATION"}:
        return "INVALID_MODEL_OUTPUT"
    if isinstance(error, TimeoutError):
        return "WORKER_TIMEOUT"
    if stage in {"GENERATION", "DECODING"}:
        return "MODEL_RUNTIME_ERROR"
    return "MODEL_RUNTIME_ERROR"
def retry_strategy_for(classification):
    return {
        "CUDA_OOM":"REDUCE_CONTEXT_OR_MODEL",
        "TOKENIZER_ERROR":"DO_NOT_RETRY",
        "CONTEXT_TOO_LARGE":"REDUCE_CONTEXT_THEN_SPLIT",
        "GENERATION_CUDA_OOM":"REDUCE_CONTEXT_THEN_SPLIT",
        "MODEL_LOAD_CUDA_OOM":"CHANGE_MEMORY_CONFIGURATION",
        "OUTPUT_TRUNCATED":"REDUCE_SCOPE_OR_SPLIT",
        "INVALID_JSON":"REQUEST_STRUCTURED_CORRECTION",
        "INVALID_MODEL_OUTPUT":"REGENERATE_CLEAN_RESPONSE",
        "WORKER_TIMEOUT":"REASSIGN_WORKER",
        "NETWORK_INTERRUPTION":"RECONNECT_AND_REASSIGN",
    }.get(classification, "MANUAL_REVIEW")

def extract_changes(text, constraints, report):
    report("PARSING", "Parsing code-safe model response", {})
    try:
        data = parse_model_response(text)
    except ValueError as error:
        raise ValueError(f"invalid model response: {error}") from error
    report("VALIDATION", "Validating structured worker result", {})
    files = data.get("files", [])
    changes = data.get("changes", [])
    if not isinstance(files, list) or not isinstance(changes, list):
        raise ValueError("files and changes must be arrays")
    if not 1 <= len(files) + len(changes) <= constraints["maximum_files"]:
        raise ValueError("model returned an invalid change count")
    for field in ("test_commands", "notes", "contract_proposals", "risks", "blocked_by", "acceptance_evidence"):
        if not isinstance(data.get(field, []), list):
            raise ValueError(f"{field} must be an array")
    result = {}
    for item in files:
        path, content = item.get("path"), item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("every generated file needs a string path and content")
        pure = pathlib.PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts or pure.parts[0].startswith("."):
            raise ValueError(f"unsafe generated path: {path}")
        result[pure.as_posix()] = content
    for item in changes:
        if not isinstance(item, dict) or item.get("operation") not in {"create", "update", "delete", "rename"}:
            raise ValueError("every change needs a supported operation")
        path = item.get("path")
        if not isinstance(path, str):
            raise ValueError("every change needs a string path")
        pure = pathlib.PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts or pure.parts[0].startswith("."):
            raise ValueError(f"unsafe generated path: {path}")
        if item["operation"] in {"create", "update"} and not isinstance(item.get("content"), str):
            raise ValueError("create/update changes require string content")
    output_bytes = sum(len(value.encode()) for value in result.values()) + sum(
        len(item.get("content", "").encode()) for item in changes
    )
    if output_bytes > constraints["maximum_output_bytes"]:
        raise ValueError("model output exceeds the controller limit")
    report("VALIDATION", f"Validated {len(files) + len(changes)} generated change(s)", {
        "file_count":len(files), "change_count":len(changes), "output_bytes":output_bytes,
    })
    return data, result

def generate_text(messages, maximum_new_tokens, report, metrics, correction_attempt=0):
    report("TOKENIZATION", "Tokenizing prompt", {})
    inputs = output = generated = None
    try:
        inputs = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_tensors="pt", return_dict=True,
        )
        input_device = model.get_input_embeddings().weight.device
        inputs = inputs.to(input_device)
        prompt_length = int(inputs["input_ids"].shape[-1])
        metrics["prompt_tokens"] = prompt_length
        if prompt_length > max_recommended_prompt_tokens:
            raise RuntimeError(
                f"PROMPT_MEMORY_LIMIT: {prompt_length} tokens exceeds measured safe "
                f"limit {max_recommended_prompt_tokens}"
            )
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        report("GENERATION", f"Generating up to {maximum_new_tokens} tokens", {
            "prompt_tokens":prompt_length,
            "measured_prompt_limit":max_recommended_prompt_tokens,
            "requested_output_tokens":maximum_new_tokens, "gpu":gpu_snapshot(),
        })
        generation = {
            "max_new_tokens":maximum_new_tokens,
            "min_new_tokens":min(32, maximum_new_tokens),
            "do_sample":False,
            "pad_token_id":tokenizer.eos_token_id,
            "repetition_penalty":1.05,
        }
        output = model.generate(**inputs, **generation)
        metrics["generated_tokens"] = int(output.shape[-1] - prompt_length)
        generated = output[0][prompt_length:]
        text = tokenizer.decode(generated, skip_special_tokens=True)
        hit_token_cap = metrics["generated_tokens"] >= maximum_new_tokens and (
            tokenizer.eos_token_id is None or int(generated[-1]) != int(tokenizer.eos_token_id)
        )
        if hit_token_cap:
            recovered = recover_completed_envelope(text)
            if recovered is None:
                metrics.update({
                    "envelope_started":"AEGAEON_RESPONSE_V1" in text,
                    "file_headers":text.count("<<<FILE:"),
                    "complete_file_markers":text.count("<<<END_FILE>>>"),
                })
                raise RuntimeError("model output truncated at maximum_new_tokens")
            text = recovered
            metrics["truncated_output_recovered"] = True
        report("DECODING", f"Decoding {metrics['generated_tokens']} generated tokens", {
            "generated_tokens":metrics["generated_tokens"],
            "truncated_output_recovered":bool(metrics.get("truncated_output_recovered")),
            "gpu":gpu_snapshot(),
        })
        return text
    finally:
        del inputs, output, generated
        gc.collect()
        torch.cuda.empty_cache()

def render_request(payload, previous_error=None):
    context = payload.get("context") or {}
    criteria = context.get("acceptance_criteria") or []
    scopes = context.get("permitted_file_scopes") or ["**/*"]
    maximum_files = int((payload.get("constraints") or {}).get("maximum_files") or 1)
    correction = (
        "\\nMANDATORY FORMAT CORRECTION\\n"
        f"Unusable: {previous_error}\\n"
        "Generate a fresh response containing exactly one small, complete file. "
        "The first characters must be AEGAEON_RESPONSE_V1. Include one FILE block, "
        "close it, emit <<<END_RESPONSE>>>, and stop. Do not explain or repeat the error.\\n"
        if previous_error else ""
    )
    return (
        "ROLE\\n" + str(payload.get("role") or "coder") + "\\n\\n"
        "TASK\\n" + str(payload.get("instructions") or "") + "\\n\\n"
        "PROJECT\\n" + str(context.get("project_summary") or "") + "\\n\\n"
        "ACCEPTANCE CRITERIA\\n- " + "\\n- ".join(str(item) for item in criteria) + "\\n\\n"
        "PERMITTED PATHS\\n- " + "\\n- ".join(str(item) for item in scopes) + "\\n\\n"
        f"OUTPUT LIMIT\\nReturn between 1 and {maximum_files} complete files. "
        "Start immediately with AEGAEON_RESPONSE_V1.\\n"
        + correction + "\\nCURRENT REPOSITORY\\n" + str(context.get("repository") or "")
    )

def infer(job, report, metrics):
    payload = job["payload"]
    last_error = None
    for attempt in range(3):
        metrics["validation_attempt"] = attempt + 1
        messages = [
            {"role":"system","content":SYSTEM},
            {"role":"user","content":render_request(payload, last_error)},
        ]
        text = generate_text(
            messages,
            payload["constraints"]["maximum_new_tokens"],
            report,
            metrics,
            correction_attempt=attempt,
        )
        try:
            return extract_changes(text, payload["constraints"], report)
        except ValueError as error:
            last_error = error
            if attempt == 2:
                break
            report("VALIDATION", f"Structured output invalid; requesting correction {attempt + 1}/2", {
                "validation_error":str(error), "output_characters":len(text),
                "envelope_started":"AEGAEON_RESPONSE_V1" in text,
                "complete_file_markers":text.count("<<<END_FILE>>>")
            })
    raise ValueError(f"model output was still invalid after 3 attempts: {last_error}")

def next_sequence():
    STATE["sequence"] += 1
    return STATE["sequence"]

def job_identity(job):
    return {
        "job_id":job["job_id"], "project_id":job["project_id"],
        "task_id":job["task_id"], "run_id":job.get("run_id"),
        "attempt_id":job.get("attempt_id"), "lease_token":job.get("lease_token", ""),
    }

def job_message(message_type, job, extra=None):
    return {"type":message_type, **job_identity(job), "sequence":next_sequence(), **(extra or {})}

def terminal_hash(data):
    canonical = dict(data)
    canonical.pop("result_hash", None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

async def safe_send(data):
    socket = CURRENT_SOCKET
    if socket is None:
        return False
    try:
        async with SEND_LOCK:
            await socket.send(json.dumps(data))
        return True
    except (ConnectionClosed, ConnectionError, OSError, RuntimeError):
        return False

async def heartbeat():
    while True:
        await asyncio.sleep(5)
        await safe_send({
            "type":"worker.heartbeat", "worker_id":WORKER_ID, "status":STATE["status"],
            "current_job_id":STATE["job_id"], "stage":STATE["stage"],
            "current_attempt_id":STATE["attempt_id"],
            "connection_generation":STATE["connection_generation"],
            "progress_sequence":STATE["sequence"],
            "progress_percent":STATE["progress"],
            "cpu_percent":psutil.cpu_percent(), "ram_percent":psutil.virtual_memory().percent,
            "gpu_percent":0, "vram_used_mb":int(torch.cuda.memory_allocated()/1024/1024),
            "vram_allocated_mb":int(torch.cuda.memory_allocated()/1024/1024),
            "vram_reserved_mb":int(torch.cuda.memory_reserved()/1024/1024),
            "vram_free_mb":int(torch.cuda.mem_get_info()[0]/1024/1024),
            "ram_available_mb":int(psutil.virtual_memory().available/1024/1024),
        })

async def execute_job(job):
    global ACTIVE_JOB, ACTIVE_TASK
    started = time.monotonic()
    metrics = {"requested_output_tokens":job.get("payload",{}).get("constraints",{}).get("maximum_new_tokens")}
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stage_history = []

    def report(stage, message, telemetry=None):
        STATE["stage"] = stage
        STATE["progress"] = max(STATE["progress"], float(STAGE_PROGRESS.get(stage, 0)))
        safe_telemetry = dict(telemetry or {})
        safe_telemetry["progress_percent"] = STATE["progress"]
        stage_history.append({"stage":stage, "message":message, "telemetry":safe_telemetry})
        loop.call_soon_threadsafe(queue.put_nowait, (stage, message, safe_telemetry))

    async def forward_logs():
        while True:
            item = await queue.get()
            if item is None:
                return
            stage, message, telemetry = item
            print(f"[{job['task_id']}] {stage}: {message}", flush=True)
            await safe_send(job_message("job.log", job, {
                "stage":stage, "message":message, "telemetry":telemetry,
            }))

    print(f"Received task {job['task_id']} ({job.get('job_type')}).", flush=True)
    await safe_send(job_message("job.started", job))
    forwarder = asyncio.create_task(forward_logs())
    try:
        report("PROMPT_PREPARATION", "Preparing model prompt", {})
        change_set, files = await asyncio.to_thread(infer, job, report, metrics)
        report("UPLOAD", f"Uploading {len(files)} generated file(s)", {})
        await asyncio.sleep(0)
        terminal = job_message("job.completed", job, {
            "artifacts":[{"type":"source_bundle","filename":portable_artifact_filename(job["task_id"]),"files":files,"changes":change_set.get("changes", [])}],
            "result":{
                "model":MODEL_ID, "runtime":"transformers", "summary":change_set.get("summary"),
                "changes":change_set.get("changes", []),
                "test_commands":change_set.get("test_commands", []),
                "notes":change_set.get("notes", []),
                "contract_proposals":change_set.get("contract_proposals", []),
                "risks":change_set.get("risks", []),
                "blocked_by":change_set.get("blocked_by", []),
                "acceptance_evidence":change_set.get("acceptance_evidence", []),
                "telemetry":{**metrics, "elapsed_seconds":round(time.monotonic()-started,3), "gpu":gpu_snapshot()},
            },
        })
        terminal["result_hash"] = terminal_hash(terminal)
        OUTBOX[str(job.get("attempt_id") or job["job_id"])] = terminal
        await safe_send(terminal)
        print(f"Completed task {job['task_id']} with {len(files)} file(s).", flush=True)
    except Exception as error:
        message = str(error).strip() or repr(error)
        classification = classify_failure(error, STATE["stage"])
        diagnostics = {
            "error_type":type(error).__name__, "message":message, "error_repr":repr(error),
            "traceback":traceback.format_exc(), "stage":STATE["stage"],
            "classification":classification,
            "retry_strategy":retry_strategy_for(classification),
            "recent_stage_history":stage_history[-20:],
            "elapsed_seconds":round(time.monotonic()-started,3),
            "prompt_tokens":metrics.get("prompt_tokens"),
            "requested_output_tokens":metrics.get("requested_output_tokens"),
            "device":str(getattr(model, "device", "unknown")), "gpu":gpu_snapshot(),
            "generation":metrics,
        }
        print("AEGAEON task failed with structured diagnostics:\\n" + json.dumps(diagnostics, indent=2), flush=True)
        terminal = job_message("job.failed", job, {
            "error":f"{diagnostics['error_type']}: {message}",
            "result":{"diagnostics":diagnostics}, "diagnostics":diagnostics,
        })
        terminal["result_hash"] = terminal_hash(terminal)
        OUTBOX[str(job.get("attempt_id") or job["job_id"])] = terminal
        await safe_send(terminal)
    finally:
        await queue.put(None)
        await forwarder
        if ACTIVE_JOB and ACTIVE_JOB.get("attempt_id") == job.get("attempt_id"):
            ACTIVE_JOB = None
            ACTIVE_TASK = None
            STATE.update({
                "status":"ready", "job_id":None, "attempt_id":None,
                "stage":"ready", "progress":0.0,
            })
        gc.collect()
        torch.cuda.empty_cache()

async def worker_session():
    global CURRENT_SOCKET, ACTIVE_JOB, ACTIVE_TASK
    url = CONTROLLER_HTTP.replace("https://", "wss://").replace("http://", "ws://") + "/ws/worker"
    memory = gpu_snapshot()
    ram = psutil.virtual_memory()
    hardware = {
        "cpu_cores": os.cpu_count() or 1,
        "ram_mb": int(ram.total/1024/1024),
        "ram_available_mb": int(ram.available/1024/1024),
        "cuda": True,
        "gpu": {"available":True,"name":gpu_name,"vram_mb":actual_vram,
                "allocated_mb":memory["allocated_mb"],"reserved_mb":memory["reserved_mb"],
                "free_mb":memory["free_mb"]},
        "gpus":[{"index":0,"name":gpu_name,"total_mb":actual_vram,
                 "allocated_mb":memory["allocated_mb"],"reserved_mb":memory["reserved_mb"],
                 "free_mb":memory["free_mb"],"utilization_percent":memory["utilization_percent"],
                 "compute_capability":compute_capability}],
    }
    registered_models = [{
        "id":MODEL_ID, "provider":"huggingface", "loaded":True,
        "runtime":"transformers", "quantization":QUANTIZATION, "status":"ready",
        "memory_footprint_mb":model_memory_mb,
        "max_recommended_prompt_tokens":max_recommended_prompt_tokens,
        "max_recommended_output_tokens":max_recommended_output_tokens,
        "memory_risk_profile":memory_risk_profile,
        "supports_cpu_offload":True, "supports_multi_gpu":False,
        "smoke_test_peak_vram_mb":smoke_peak_mb,
    }]
    if MODEL_ID != REQUESTED_MODEL_ID:
        registered_models.append({
            "id":REQUESTED_MODEL_ID,
            "provider":"huggingface",
            "loaded":False,
            "runtime":"transformers",
            "quantization":QUANTIZATION,
            "status":"incompatible",
            "last_error":(
                f"Resource governor selected {MODEL_ID}; {REQUESTED_MODEL_ID} "
                f"does not retain the required VRAM reserve on {gpu_name}."
            ),
        })
    async with websockets.connect(
        url, max_size=4_000_000,
        ping_interval=30, ping_timeout=180, close_timeout=10,
        additional_headers={"X-AEGAEON-Worker-Token": WORKER_TOKEN},
    ) as socket:
        CURRENT_SOCKET = socket
        active_attempts = []
        if ACTIVE_JOB is not None:
            active_attempts.append({
                **job_identity(ACTIVE_JOB), "stage":STATE["stage"],
                "progress_sequence":STATE["sequence"],
            })
        await socket.send(json.dumps({
            "type":"worker.register", "worker_id":WORKER_ID,
            "session_id":SESSION_ID, "protocol_version":2,
            "connection_generation":STATE["connection_generation"],
            "active_attempts":active_attempts,
            "completed_attempts":[{
                "job_id":item.get("job_id"), "attempt_id":item.get("attempt_id"),
                "result_hash":item.get("result_hash"),
            } for item in OUTBOX.values()],
            "hostname":WORKER_NAME,
            "hardware":hardware,
            "capabilities":["cuda","model.generate","model.repair","artifact.create"],
            "models":registered_models,
        }))
        beat = asyncio.create_task(heartbeat())
        registered = False
        try:
            async for raw in socket:
                job = json.loads(raw)
                if job.get("type") == "worker.registered":
                    STATE["connection_generation"] = int(job.get("connection_generation") or 0)
                    if not registered:
                        print("AEGAEON worker online. Waiting for tasks...", flush=True)
                    registered = True
                    for terminal in list(OUTBOX.values()):
                        await safe_send(terminal)
                elif job.get("type") == "protocol.error":
                    print("Controller rejected a worker message: " + str(job.get("message")), flush=True)
                elif job.get("type") == "job.assign":
                    attempt_id = str(job.get("attempt_id") or job["job_id"])
                    if ACTIVE_JOB is not None:
                        active_id = str(ACTIVE_JOB.get("attempt_id") or ACTIVE_JOB["job_id"])
                        if active_id == attempt_id:
                            await safe_send(job_message("job.ack", ACTIVE_JOB))
                        else:
                            await safe_send({
                                "type":"protocol.error", "job_id":job["job_id"],
                                "message":"worker slot is already occupied",
                            })
                        continue
                    if int(job.get("connection_generation") or 0) != STATE["connection_generation"]:
                        await safe_send({
                            "type":"protocol.error", "job_id":job["job_id"],
                            "message":"job offer uses a stale connection generation",
                        })
                        continue
                    ACTIVE_JOB = job
                    STATE.update({
                        "status":"busy", "job_id":job["job_id"],
                        "attempt_id":attempt_id, "stage":"assigned", "progress":2.0,
                    })
                    await safe_send(job_message("job.ack", job))
                    ACTIVE_TASK = asyncio.create_task(execute_job(job))
                elif job.get("type") == "job.cancel":
                    if ACTIVE_JOB is not None and (
                        job.get("attempt_id") == ACTIVE_JOB.get("attempt_id")
                        and job.get("lease_token") == ACTIVE_JOB.get("lease_token")
                    ):
                        task = ACTIVE_TASK
                        if task is not None and not task.done():
                            task.cancel()
                        await safe_send(job_message("job.cancelled", ACTIVE_JOB, {
                            "reason":str(job.get("reason") or "cancelled")
                        }))
                elif job.get("type") == "job.result.ack":
                    attempt_id = str(job.get("attempt_id") or "")
                    terminal = OUTBOX.get(attempt_id)
                    if terminal is not None and (
                        not job.get("result_hash")
                        or job.get("result_hash") == terminal.get("result_hash")
                    ):
                        OUTBOX.pop(attempt_id, None)
        finally:
            beat.cancel()
            try:
                await beat
            except asyncio.CancelledError:
                pass
            if CURRENT_SOCKET is socket:
                CURRENT_SOCKET = None

async def run_worker():
    delay = 1
    while True:
        try:
            await worker_session()
            delay = 1
        except ConnectionClosed as error:
            if error.code == 4009:
                print(
                    "This notebook execution was superseded by another worker session. "
                    "Stopping this duplicate connection.",
                    flush=True,
                )
                return
            if error.code == 1008:
                print(
                    "The worker credential is invalid, expired, or revoked. "
                    "Download a fresh notebook and use its new pairing code.",
                    flush=True,
                )
                return
            wait = random.uniform(max(0.5, delay / 2), delay)
            print(
                f"Worker connection closed ({error.code}: {error.reason or 'no reason'}). "
                f"Retrying in {wait:.1f}s...",
                flush=True,
            )
            await asyncio.sleep(wait)
            delay = min(30, delay * 2)
        except Exception as error:
            detail = str(error).strip() or repr(error)
            print(
                "\\nAEGAEON could not establish the worker connection.\\n"
                f"Controller: {CONTROLLER_HTTP}\\n"
                "The secure connection may have expired. AEGAEON will retry automatically.\\n"
                "If this continues, return to AEGAEON and select Reconnect Remote Workers.\\n"
                f"Technical detail: {type(error).__name__}: {detail}",
                flush=True,
            )
            wait = random.uniform(max(0.5, delay / 2), delay)
            await asyncio.sleep(wait)
            delay = min(30, delay * 2)

nest_asyncio.apply()
asyncio.get_event_loop().run_until_complete(run_worker())
'''
        worker = worker.replace("__MODEL_RESPONSE_PARSER__", PORTABLE_MODEL_RESPONSE_PARSER_SOURCE)
        cells = [
            (
                "markdown",
                [
                    "# AEGAEON Broke Boy Worker\\n",
                    "1. Enable a GPU runtime.\\n",
                    "2. Click **Runtime -> Run all**.\\n",
                    "3. Enter the pairing code shown in AEGAEON.\\n",
                    "4. Leave this notebook running.\\n\\n",
                    "AEGAEON handles the rest.\\n",
                ],
            ),
            ("code", install.splitlines(True)),
            ("code", setup.splitlines(True)),
            ("code", load.splitlines(True)),
            ("code", worker.splitlines(True)),
        ]
        return {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "colab": {"name": f"AEGAEON {spec.worker_name}", "provenance": []},
                "kernelspec": {"name": "python3", "display_name": "Python 3"},
                "accelerator": "GPU",
            },
            "cells": [
                {
                    "cell_type": kind,
                    "metadata": {},
                    **(
                        {"source": source}
                        if kind == "markdown"
                        else {"execution_count": None, "outputs": [], "source": source}
                    ),
                }
                for kind, source in cells
            ],
        }
