from __future__ import annotations

import shutil
import subprocess
from typing import Any

import psutil

_GPU_QUERY = "index,name,memory.total,memory.used,memory.free,utilization.gpu,compute_cap"


def _nvidia_rows() -> list[list[str]]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={_GPU_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return [
            [part.strip() for part in line.split(",")]
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ]
    except (OSError, subprocess.SubprocessError):
        return []


def _gpu_snapshots() -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for row in _nvidia_rows():
        if len(row) != 7:
            continue
        try:
            index, name, total, used, free, utilization, capability = row
            snapshots.append(
                {
                    "available": True,
                    "index": int(index),
                    "name": name,
                    "vram_mb": int(total),
                    "total_mb": int(total),
                    "allocated_mb": int(used),
                    "reserved_mb": int(used),
                    "free_mb": int(free),
                    "utilization_percent": float(utilization),
                    "compute_capability": capability,
                }
            )
        except ValueError:
            continue
    return snapshots


def detect_hardware() -> dict[str, Any]:
    gpus = _gpu_snapshots()
    gpu = (
        gpus[0]
        if gpus
        else {
            "available": False,
            "index": 0,
            "name": None,
            "vram_mb": 0,
            "total_mb": 0,
            "allocated_mb": 0,
            "reserved_mb": 0,
            "free_mb": 0,
            "utilization_percent": 0,
            "compute_capability": None,
        }
    )
    memory = psutil.virtual_memory()
    return {
        "cpu_cores": psutil.cpu_count(logical=True) or 1,
        "ram_mb": int(memory.total / 1024 / 1024),
        "ram_available_mb": int(memory.available / 1024 / 1024),
        "gpu": gpu,
        "gpus": gpus,
        "cuda": bool(gpus),
        "storage_mb": int(shutil.disk_usage(".").free / 1024 / 1024),
    }


def utilization() -> dict[str, float | int]:
    memory = psutil.virtual_memory()
    gpus = _gpu_snapshots()
    allocated = sum(int(gpu["allocated_mb"]) for gpu in gpus)
    reserved = sum(int(gpu["reserved_mb"]) for gpu in gpus)
    free = sum(int(gpu["free_mb"]) for gpu in gpus)
    weighted_utilization = (
        sum(float(gpu["utilization_percent"]) for gpu in gpus) / len(gpus) if gpus else 0
    )
    return {
        "cpu_percent": psutil.cpu_percent(),
        "ram_percent": memory.percent,
        "ram_available_mb": int(memory.available / 1024 / 1024),
        "gpu_percent": round(weighted_utilization, 2),
        "vram_used_mb": allocated,
        "vram_allocated_mb": allocated,
        "vram_reserved_mb": reserved,
        "vram_free_mb": free,
    }


def detect_capabilities() -> list[str]:
    capabilities = ["python"]
    for command in ("git", "node", "npm"):
        if shutil.which(command):
            capabilities.append(command)
    if shutil.which("nvidia-smi"):
        capabilities.extend(["cuda", "gpu"])
    return sorted(set(capabilities))
