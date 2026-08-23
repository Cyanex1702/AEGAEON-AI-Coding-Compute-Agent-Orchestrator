# ruff: noqa: E501
from __future__ import annotations

import json
from dataclasses import dataclass

from aegaeon.artifacts.manager import ArtifactManager
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
    connection_generation: int = 0


class ColabNotebookGenerator:
    """Deterministic Run-All notebook template for one complete model per worker."""

    def __init__(self, artifacts: ArtifactManager, pairing: WorkerPairingService) -> None:
        self.artifacts = artifacts
        self.pairing = pairing

    def generate(self, spec: NotebookSpec) -> NotebookRead:
        code, expires = self.pairing.create(spec.project_id, spec.worker_name, spec.model_id)
        artifact = self.artifacts.create(
            spec.project_id, None, "worker_notebook", f"{spec.worker_name}.ipynb",
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
        install = """!pip -q install \"transformers>=4.46\" \"accelerate>=1\" \"bitsandbytes>=0.44\" \"websockets>=14,<16\" psutil"""
        setup = """import getpass, json, urllib.error, urllib.request

CONTROLLER_HTTP = "__CONTROLLER__".rstrip("/")
MODEL_ID = "__MODEL__"
WORKER_NAME = "__WORKER__"
QUANTIZATION = "__QUANTIZATION__"
MINIMUM_VRAM_MB = __MINIMUM_VRAM__
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
        )
        load = """import shutil, subprocess, torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

if not shutil.which("nvidia-smi"):
    raise RuntimeError("No NVIDIA GPU detected. Choose a GPU runtime and run again.")
query = subprocess.run(
    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
    check=True, capture_output=True, text=True,
).stdout.strip().splitlines()[0]
gpu_name, actual_vram = [part.strip() for part in query.rsplit(",", 1)]
actual_vram = int(actual_vram)
if actual_vram < MINIMUM_VRAM_MB:
    raise RuntimeError(
        f"This model needs about {MINIMUM_VRAM_MB} MB VRAM; this runtime has {actual_vram} MB."
    )
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
print("Model loaded. AEGAEON is ready to use this GPU.")
"""
        worker = '''import asyncio, json, os, pathlib, re
import psutil, websockets

SYSTEM = """You are an AEGAEON coding worker. Return only JSON matching:
{"summary":"...","files":[{"path":"relative/path","content":"..."}],"test_commands":[],"notes":[]}.
Paths must be repository-relative. Never use hidden control paths, .git, absolute paths, or .. ."""

def extract_changes(text, constraints):
    fence = chr(96) * 3
    match = re.search(re.escape(fence) + r"(?:json)?\\s*(\\{.*\\})\\s*" + re.escape(fence), text, re.S)
    try:
        data = json.loads(match.group(1) if match else text)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}") from error
    files = data.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= constraints["maximum_files"]:
        raise ValueError("model returned an invalid file list")
    result = {}
    for item in files:
        path, content = item.get("path"), item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("every generated file needs a string path and content")
        pure = pathlib.PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts or pure.parts[0].startswith("."):
            raise ValueError(f"unsafe generated path: {path}")
        result[pure.as_posix()] = content
    if sum(len(value.encode()) for value in result.values()) > constraints["maximum_output_bytes"]:
        raise ValueError("model output exceeds the controller limit")
    return data, result

def generate_text(messages, maximum_new_tokens):
    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True, return_tensors="pt"
    ).to(model.device)
    output = model.generate(
        inputs, max_new_tokens=maximum_new_tokens,
        do_sample=False, pad_token_id=tokenizer.eos_token_id,
    )
    return tokenizer.decode(output[0][inputs.shape[-1]:], skip_special_tokens=True)

def infer(job):
    payload = job["payload"]
    messages = [
        {"role":"system","content":SYSTEM},
        {"role":"user","content":json.dumps(payload, indent=2)},
    ]
    last_error = None
    for attempt in range(3):
        text = generate_text(messages, payload["constraints"]["maximum_new_tokens"])
        try:
            return extract_changes(text, payload["constraints"])
        except ValueError as error:
            last_error = error
            if attempt == 2:
                break
            messages.extend([
                {"role":"assistant","content":text},
                {"role":"user","content":(
                    "Validation failed: " + str(error) + "\\nReturn corrected JSON only. "
                    'Required schema: {"summary":"...","files":[{"path":"relative/path",'
                    '"content":"..."}],"test_commands":[],"notes":[]}.'
                )},
            ])
    raise ValueError(f"model output was still invalid after 3 attempts: {last_error}")

async def heartbeat(socket):
    while True:
        await asyncio.sleep(5)
        await socket.send(json.dumps({
            "type":"worker.heartbeat", "worker_id":WORKER_ID, "status":"ready",
            "cpu_percent":psutil.cpu_percent(), "ram_percent":psutil.virtual_memory().percent,
            "gpu_percent":0, "vram_used_mb":int(torch.cuda.memory_allocated()/1024/1024),
        }))

async def worker_session():
    url = CONTROLLER_HTTP.replace("https://", "wss://").replace("http://", "ws://") + "/ws/worker"
    hardware = {
        "cpu_cores": os.cpu_count() or 1, "ram_mb": int(psutil.virtual_memory().total/1024/1024),
        "cuda": True, "gpu": {"available":True,"name":gpu_name,"vram_mb":actual_vram},
    }
    async with websockets.connect(
        url, max_size=4_000_000,
        additional_headers={"X-AEGAEON-Worker-Token": WORKER_TOKEN},
    ) as socket:
        await socket.send(json.dumps({
            "type":"worker.register", "worker_id":WORKER_ID, "hostname":WORKER_NAME,
            "hardware":hardware,
            "capabilities":["cuda","model.generate","model.repair","artifact.create"],
            "models":[{"id":MODEL_ID,"provider":"huggingface","loaded":True,
                "runtime":"transformers","quantization":QUANTIZATION,"status":"ready"}],
        }))
        beat = asyncio.create_task(heartbeat(socket))
        try:
            async for raw in socket:
                job = json.loads(raw)
                if job.get("type") != "job.assign":
                    continue
                common = {"job_id":job["job_id"],"project_id":job["project_id"],"task_id":job["task_id"]}
                await socket.send(json.dumps({"type":"job.started", **common}))
                try:
                    change_set, files = await asyncio.to_thread(infer, job)
                    await socket.send(json.dumps({
                        "type":"job.completed", **common,
                        "artifacts":[{"type":"source_bundle","filename":job["task_id"]+".json","files":files}],
                        "result":{"model":MODEL_ID,"runtime":"transformers","summary":change_set.get("summary")},
                    }))
                except Exception as error:
                    await socket.send(json.dumps({"type":"job.failed",**common,"error":str(error),"result":{}}))
        finally:
            beat.cancel()

async def run_worker():
    delay = 1
    while True:
        try:
            await worker_session()
        except Exception as error:
            print(
                "\\nAEGAEON could not establish the worker connection.\\n"
                f"Controller: {CONTROLLER_HTTP}\\n"
                "The secure connection may have expired. AEGAEON will retry automatically.\\n"
                "If this continues, return to AEGAEON and select Reconnect Remote Workers.\\n"
                f"Technical detail: {error}"
            )
            await asyncio.sleep(delay)
            delay = min(15, delay * 2)

await run_worker()
'''
        cells = [
            ("markdown", [
                "# AEGAEON Broke Boy Worker\\n",
                "1. Enable a GPU runtime.\\n",
                "2. Click **Runtime -> Run all**.\\n",
                "3. Enter the pairing code shown in AEGAEON.\\n",
                "4. Leave this notebook running.\\n\\n",
                "AEGAEON handles the rest.\\n",
            ]),
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
