from __future__ import annotations

import shutil
import subprocess
from typing import Any

import psutil


def detect_hardware() -> dict[str, Any]:
    gpu: dict[str, Any] = {"available": False, "name": None, "vram_mb": 0}
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            first = result.stdout.strip().splitlines()[0]
            name, vram = [part.strip() for part in first.rsplit(",", 1)]
            gpu = {"available": True, "name": name, "vram_mb": int(vram)}
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
    return {
        "cpu_cores": psutil.cpu_count(logical=True) or 1,
        "ram_mb": int(psutil.virtual_memory().total / 1024 / 1024),
        "gpu": gpu,
        "cuda": bool(gpu["available"]),
        "storage_mb": int(shutil.disk_usage(".").free / 1024 / 1024),
    }


def utilization() -> dict[str, float | int]:
    return {
        "cpu_percent": psutil.cpu_percent(),
        "ram_percent": psutil.virtual_memory().percent,
        "gpu_percent": 0,
        "vram_used_mb": 0,
    }


def detect_capabilities() -> list[str]:
    capabilities = ["python"]
    for command in ("git", "node", "npm"):
        if shutil.which(command):
            capabilities.append(command)
    if shutil.which("nvidia-smi"):
        capabilities.extend(["cuda", "gpu"])
    return sorted(set(capabilities))
