"""Explicit public-model downloads and conservative hardware advice."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path


def hardware():
    total = available = None
    try:
        if os.name == "nt":

            class Memory(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong)
                    for name in (
                        "total",
                        "available",
                        "page",
                        "free_page",
                        "virtual",
                        "free_virtual",
                        "extended",
                    )
                ]

            memory = Memory()
            memory.length = ctypes.sizeof(memory)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                total, available = memory.total, memory.available
        else:
            info = dict(
                line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
            )
            total = int(info["MemTotal"].split()[0]) * 1024
            available = int(info["MemAvailable"].split()[0]) * 1024
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    gpus = []
    binary = shutil.which("nvidia-smi")
    if binary:
        try:
            result = subprocess.run(
                [
                    binary,
                    "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            for line in result.stdout.splitlines():
                name, size, free = line.rsplit(",", 2)
                gpus.append({"name": name.strip(), "total_mb": int(size), "free_mb": int(free)})
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return {"ram_bytes": total, "available_ram_bytes": available, "gpus": gpus}


def advice(model, machine):
    # Conservative planning allowance at modest context, not a measured requirement.
    estimate = model["bytes"] + 2 * 1024**3
    available = machine.get("available_ram_bytes")
    fit = "unknown" if available is None else "may_fit" if available >= estimate else "tight"
    gpu = any(g["free_mb"] * 1024**2 >= estimate for g in machine.get("gpus", []))
    return {
        "fit": fit,
        "estimated_runtime_bytes": estimate,
        "execution": "GPU may fit" if gpu else "CPU or partial GPU offload; speed unmeasured",
        "notice": "Memory estimate only, at modest context. Fitting does not establish coding "
        "or reasoning quality. Close other apps or use cloud compute if memory is tight.",
    }


class Models:
    def __init__(self, directory):
        self.directory = Path(directory).resolve() / "models"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.catalog = json.loads(Path(__file__).with_name("model_catalog.json").read_text())
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.state = {"status": "idle"}
        self.thread = None

    def item(self, identifier):
        item = next((m for m in self.catalog if m["id"] == identifier), None)
        if item is None:
            raise ValueError("Unknown catalog model")
        return item

    def listing(self):
        machine = hardware()
        items = []
        for model in self.catalog:
            path = self.directory / model["file"]
            items.append(
                {
                    **model,
                    "advice": advice(model, machine),
                    "downloaded": path.is_file() and path.stat().st_size == model["bytes"],
                    "source": "https://huggingface.co/" + model["repo"],
                }
            )
        with self.lock:
            return {
                "hardware": machine,
                "models": items,
                "download": dict(self.state),
                "runtime_found": bool(shutil.which("llama-server")),
                "notice": "Downloads install weights only. Run an official llama.cpp runtime "
                "with the generated command, then connect its endpoint in Model settings. "
                "Thinking support depends on the model and runtime settings; no automatic "
                "reasoning improvement is claimed.",
            }

    def start(self, identifier):
        model = self.item(identifier)
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("A model download is already active")
            if shutil.disk_usage(self.directory).free < model["bytes"] + 256 * 1024**2:
                raise ValueError("Not enough free disk space for this download")
            self.cancel_event = threading.Event()
            self.state = {
                "status": "downloading",
                "id": identifier,
                "received": 0,
                "total": model["bytes"],
            }
            self.thread = threading.Thread(target=self.download, args=(model,), daemon=True)
            self.thread.start()
            return dict(self.state)

    def download(self, model):
        path = self.directory / model["file"]
        temporary = path.with_suffix(".partial")
        try:
            url = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{model['file']}"
            checksum, received = hashlib.sha256(), 0
            with urllib.request.urlopen(url, timeout=30) as response, temporary.open("wb") as out:
                while block := response.read(256 * 1024):
                    if self.cancel_event.is_set():
                        raise InterruptedError("Download cancelled")
                    received += len(block)
                    if received > model["bytes"]:
                        raise ValueError("Download exceeds pinned size")
                    checksum.update(block)
                    out.write(block)
                    with self.lock:
                        self.state["received"] = received
            if self.cancel_event.is_set():
                raise InterruptedError("Download cancelled")
            if received != model["bytes"] or checksum.hexdigest() != model["sha256"]:
                raise ValueError("Download size/checksum mismatch; model was not installed")
            temporary.replace(path)
            with self.lock:
                self.state["status"] = "downloaded"
        except (OSError, ValueError) as exc:
            temporary.unlink(missing_ok=True)
            with self.lock:
                self.state.update(
                    status="cancelled" if isinstance(exc, InterruptedError) else "failed",
                    error=str(exc),
                )

    def cancel(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                return {"status": self.state["status"]}
            self.cancel_event.set()
            self.state["status"] = "cancelling"
            return {"status": "cancelling"}

    def command(self, identifier):
        model = self.item(identifier)
        path = self.directory / model["file"]
        if not path.is_file() or path.stat().st_size != model["bytes"]:
            raise ValueError("Download this model first")
        checksum = hashlib.sha256()
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                checksum.update(block)
        if checksum.hexdigest() != model["sha256"]:
            raise ValueError("Model checksum changed; download again before use")
        return {
            "argv": [
                shutil.which("llama-server") or "llama-server",
                "-m",
                str(path),
                "--alias",
                "local",
                "--host",
                "127.0.0.1",
                "--port",
                "8080",
                "-c",
                "4096",
                "-ngl",
                "0",
            ],
            "notice": "Conservative CPU launch, 4K context. No command was executed. "
            "Choose a free port if 8080 is occupied. Configure the matching "
            "endpoint and context in Model settings; hardware fit is not a quality score.",
        }
