"""User-operated notebook helpers. No public endpoint or remote worker is created."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

from .bundles import pack_result, restore_checkpoint, unpack_task
from .checks import stop_tree
from .cli import event
from .provider import ModelProvider
from .session import Session

LLAMA_REVISION = "4413a192410fad1f054e9216114087a3e01527a0"
MODEL_REPOSITORY = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
MODEL_REVISION = "13fb94bfda8c8cf22497dc57b78f391a9acb426a"
MODEL_FILE = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
MODEL_SHA256 = "509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c"
MODEL_BYTES = 4_683_073_536


# Official, immutable release assets with their published SHA256 digests.
PREBUILT_TAG = "b11982"
PREBUILT_ASSETS = (
    (
        "llama-b11982-bin-ubuntu-cuda-12.8-x64.tar.gz",
        182530528,
        "c1d210f1060afe186148b19a32c079aefc19c715cece56125df6354ab759810d",
    ),
    (
        "cudart-llama-b11982-bin-ubuntu-cuda-12.8-x64.tar.gz",
        594371574,
        "ca24c61ecf5ad7c0984850114130760e0aaa87accca14b41c242e25b59b18e84",
    ),
)


def _prebuilt_runtime(runtime_root: Path) -> tuple[Path, str]:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Official prebuilt notebook runtime needs Linux x86_64")
    destination = runtime_root / PREBUILT_TAG
    destination.mkdir(exist_ok=True)
    for name, size, expected in PREBUILT_ASSETS:
        archive = runtime_root / name
        if not archive.is_file():
            temporary = archive.with_suffix(".download")
            print(f"Downloading official runtime asset: {name}", flush=True)
            url = f"https://github.com/ggml-org/llama.cpp/releases/download/{PREBUILT_TAG}/{name}"
            written = 0
            checksum = hashlib.sha256()
            with (
                urllib.request.urlopen(url, timeout=120) as response,
                temporary.open("wb") as target,
            ):
                while block := response.read(4_000_000):
                    written += len(block)
                    if written > size:
                        raise RuntimeError("runtime download exceeds pinned size")
                    checksum.update(block)
                    target.write(block)
            if written != size or checksum.hexdigest() != expected:
                raise RuntimeError("runtime download checksum mismatch")
            temporary.replace(archive)
        checksum = hashlib.sha256()
        with archive.open("rb") as existing:
            while block := existing.read(4_000_000):
                checksum.update(block)
        if archive.stat().st_size != size or checksum.hexdigest() != expected:
            raise RuntimeError("cached runtime asset checksum mismatch")
        with tarfile.open(archive, "r:gz") as bundle:
            # Python's data filter rejects escaping links and unsafe tar paths.
            bundle.extractall(destination, filter="data")
    binaries = list(destination.rglob("llama-server"))
    if len(binaries) != 1:
        raise RuntimeError("official runtime archive has no unique llama-server")
    libraries = sorted({str(p.parent) for p in destination.rglob("*.so*")})
    # Colab exposes the host's libcuda driver through LD_LIBRARY_PATH.
    # Replacing that path can silently leave only a CPU backend available.
    inherited = os.environ.get("LD_LIBRARY_PATH", "")
    return binaries[0], ":".join([*libraries, *([inherited] if inherited else [])])


def _require_cuda(binary: Path, env: dict) -> None:
    result = subprocess.run(
        [str(binary), "--list-devices"], env=env, capture_output=True, text=True, timeout=30
    )
    if result.returncode or "CUDA0:" not in result.stdout:
        raise RuntimeError(
            "CUDA backend is unavailable; refusing silent CPU fallback. " + result.stderr[-2000:]
        )


def gpu_info() -> dict:
    if not shutil.which("nvidia-smi"):
        raise RuntimeError("Enable a GPU accelerator in the notebook settings first")
    process = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free,compute_cap",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if process.returncode:
        raise RuntimeError("nvidia-smi failed; inspect the notebook accelerator")
    rows = [row.split(",") for row in process.stdout.strip().splitlines()]
    if not rows:
        raise RuntimeError("No available GPU was reported")
    name, total, free, capability = (x.strip() for x in rows[0])
    return {
        "name": name,
        "total_mb": int(total),
        "free_mb": int(free),
        "compute_capability": capability.replace(".", ""),
    }


def _model_port(port: int) -> int:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("model port must be 0..65535; 0 selects a free port")
    # Colab can already have a service on 8080. Never stop or reuse that service.
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError("Requested model port is already in use") from None
        return probe.getsockname()[1]


def _runtime_command(pid: int) -> bytes:
    return (Path("/proc") / str(pid) / "cmdline").read_bytes()


def _reject_running_model(runtime_root: Path) -> None:
    metadata = runtime_root / "runtime.json"
    if not metadata.is_file():
        return
    previous = json.loads(metadata.read_text())
    if not isinstance(previous, dict):
        raise RuntimeError("Invalid saved runtime metadata; choose a fresh runtime folder")
    pid = previous.get("pid")
    if type(pid) is not int or pid <= 1:
        return
    try:
        command = _runtime_command(pid)
    except (FileNotFoundError, ProcessLookupError):
        return
    if b"llama-server" in command and str(runtime_root).encode() in command:
        raise RuntimeError(
            "This runtime already has a model server. Stop inference before rerunning setup."
        )


def setup_model(
    runtime_root: Path, context: int = 8192, *, port: int = 0, build_from_source: bool = False
) -> dict:
    """Load a pinned official runtime and checksummed model; source build is opt-in."""
    # Reject invalid context before probing hardware, downloading or building.
    provider = ModelProvider(context_tokens=context, output_tokens=min(2048, context // 4))
    runtime_root = runtime_root.expanduser().resolve()
    _reject_running_model(runtime_root)
    selected_port = _model_port(port)
    endpoint = f"http://127.0.0.1:{selected_port}/v1"
    provider.base_url = endpoint
    info = gpu_info()
    if info["free_mb"] < 7500:
        raise RuntimeError("Need at least 7.5 GB free VRAM for the starter's warmup")
    if build_from_source:
        for command in ("nvcc", "git", "cmake", "g++"):
            if not shutil.which(command):
                raise RuntimeError(f"Missing {command}; source builds need CUDA/build tools")
    runtime_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(runtime_root).free < 8_000_000_000:
        raise RuntimeError("Need at least 8 GB free disk for model, runtime and workspace")
    if Path("/proc/meminfo").is_file():
        available = next(
            int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        if available < 5_500_000_000:
            raise RuntimeError("Need at least 5.5 GB available system RAM before model loading")
    if build_from_source:
        binary = _build_runtime(runtime_root, info)
        library_path = os.environ.get("LD_LIBRARY_PATH", "")
    else:
        binary, library_path = _prebuilt_runtime(runtime_root)
    model_env = {**os.environ, "CUDA_VISIBLE_DEVICES": "0", "LD_LIBRARY_PATH": library_path}
    _require_cuda(binary, model_env)
    weights = runtime_root / MODEL_FILE
    if not weights.exists():
        temporary = weights.with_suffix(".download")
        print("Downloading only the selected 4-bit weights (~4.7 GB).", flush=True)
        url = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{MODEL_FILE}"
        checksum = hashlib.sha256()
        written = 0
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as target:
            while block := response.read(4_000_000):
                written += len(block)
                if written > MODEL_BYTES:
                    raise RuntimeError("model download exceeds expected size")
                checksum.update(block)
                target.write(block)
        if written != MODEL_BYTES or checksum.hexdigest() != MODEL_SHA256:
            raise RuntimeError("model download checksum mismatch; delete .download and retry")
        temporary.replace(weights)
    else:
        checksum = hashlib.sha256()
        with weights.open("rb") as existing:
            while block := existing.read(4_000_000):
                checksum.update(block)
        if weights.stat().st_size != MODEL_BYTES or checksum.hexdigest() != MODEL_SHA256:
            raise RuntimeError("cached model checksum mismatch")
    log = runtime_root / "model-server.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [
                str(binary),
                "-m",
                str(weights),
                "-ngl",
                "99",
                "--device",
                "CUDA0",
                "-c",
                str(context),
                "-np",
                "1",
                "-b",
                "128",
                "-ub",
                "64",
                "--host",
                "127.0.0.1",
                "--port",
                str(selected_port),
                "--alias",
                "local",
                "--jinja",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=model_env,
        )
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Model server exited; inspect " + str(log))
            try:
                with urllib.request.urlopen(
                    endpoint.removesuffix("/v1") + "/health", timeout=3
                ) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("Model startup timed out; inspect " + str(log))
        from .validation import probe_model

        if probe_model(provider)["status"] != "passed":
            raise RuntimeError(
                "Model warmup failed the fixed action-JSON probe; inspect " + str(log)
            )
        after = gpu_info()
        if info["free_mb"] - after["free_mb"] < 2048:
            raise RuntimeError("Model did not show expected GPU allocation; inspect " + str(log))
        if after["free_mb"] < 2048:
            raise RuntimeError("Warmup leaves less than 2 GB VRAM headroom; reduce context/model")
    except BaseException:
        process.terminate()
        process.wait(timeout=15)
        raise
    result = {
        "pid": process.pid,
        "endpoint": endpoint,
        "model": "local",
        "context": context,
        "gpu": after,
        "gpu_offload_mb": info["free_mb"] - after["free_mb"],
        "llama_revision": LLAMA_REVISION if build_from_source else PREBUILT_TAG,
        "runtime_kind": "source_build" if build_from_source else "official_prebuilt_cuda",
        "model_revision": MODEL_REVISION,
        "model_sha256": MODEL_SHA256,
        "log": str(log),
    }
    (runtime_root / "runtime.json").write_text(json.dumps(result, indent=2))
    return result


def _run_build(argv: list[str], *, timeout: int, log: Path) -> None:
    with log.open("ab") as output:
        process = subprocess.Popen(
            argv, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            status = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stop_tree(process)
            raise RuntimeError(
                "Runtime build stopped with its child processes. Inspect " + str(log)
            ) from None
    if status:
        with log.open("rb") as saved:
            saved.seek(max(0, log.stat().st_size - 4000))
            tail = saved.read().decode(errors="replace")
        raise RuntimeError("Runtime build failed. Inspect " + str(log) + "\n" + tail)


def _build_runtime(runtime_root: Path, info: dict) -> Path:
    log = runtime_root / "runtime-build.log"
    source = runtime_root / "llama.cpp"
    if not source.exists():
        _run_build(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "https://github.com/ggml-org/llama.cpp",
                str(source),
            ],
            log=log,
            timeout=300,
        )
    _run_build(["git", "-C", str(source), "checkout", LLAMA_REVISION], log=log, timeout=120)
    binary = source / "build" / "bin" / "llama-server"
    if not binary.is_file():
        print("Building the pinned CUDA runtime. This can take several minutes.", flush=True)
        _run_build(
            [
                "cmake",
                "-S",
                str(source),
                "-B",
                str(source / "build"),
                "-DGGML_CUDA=ON",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_CUDA_ARCHITECTURES=" + info["compute_capability"],
            ],
            log=log,
            timeout=300,
        )
        _run_build(
            [
                "cmake",
                "--build",
                str(source / "build"),
                "--target",
                "llama-server",
                "--config",
                "Release",
                "-j",
                "2",
            ],
            log=log,
            timeout=1800,
        )
    return binary


def run_uploaded_task(
    bundle: Path,
    destination: Path,
    output: Path,
    *,
    allow_checks: bool = False,
    resume_result: Path | None = None,
    provider=None,
) -> dict:
    root, manifest = unpack_task(bundle, destination)
    task = dict(manifest["task"])
    if resume_result:
        previous = restore_checkpoint(root, manifest, resume_result)
        task["resume_notes"] = {
            "summary": previous.get("summary", ""),
            "unresolved": previous.get("unresolved", []),
        }
    session = Session(
        root, task, provider or ModelProvider(), allow_checks=allow_checks, on_event=event
    )
    result = session.run()
    pack_result(root, manifest, result, output)
    print("Download the result now. Files only in notebook temporary storage are not durable.")
    return result
