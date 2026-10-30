import hashlib
import io
import json
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from aegaeon_lite import notebook


def archive_bytes(name, data):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        entry = tarfile.TarInfo(name)
        entry.size = len(data)
        entry.mode = 0o755
        archive.addfile(entry, io.BytesIO(data))
    return result.getvalue()


def fixture_assets(monkeypatch, *, unsafe=False):
    items = {
        "runtime.tar.gz": archive_bytes("../escape" if unsafe else "bin/llama-server", b"binary"),
        "cuda.tar.gz": archive_bytes("lib/libcuda-test.so", b"library"),
    }
    monkeypatch.setattr(notebook.platform, "system", lambda: "Linux")
    monkeypatch.setattr(notebook.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        notebook,
        "PREBUILT_ASSETS",
        tuple((name, len(data), hashlib.sha256(data).hexdigest()) for name, data in items.items()),
    )
    monkeypatch.setattr(
        notebook.urllib.request,
        "urlopen",
        lambda url, **kwargs: io.BytesIO(items[url.rsplit("/", 1)[-1]]),
    )
    return items


def test_prebuilt_assets_are_verified_extracted_and_cached(tmp_path, monkeypatch):
    fixture_assets(monkeypatch)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/colab-driver-libraries")
    binary, libraries = notebook._prebuilt_runtime(tmp_path)
    assert binary.read_bytes() == b"binary"
    assert (tmp_path / notebook.PREBUILT_TAG / "lib/libcuda-test.so").is_file()
    assert str(binary.parent.parent / "lib") in libraries
    assert libraries.endswith(":/colab-driver-libraries")

    def forbidden(*args, **kwargs):
        raise AssertionError("Verified cache must not download again")

    monkeypatch.setattr(notebook.urllib.request, "urlopen", forbidden)
    assert notebook._prebuilt_runtime(tmp_path)[0] == binary


def test_tampered_cached_runtime_is_rejected(tmp_path, monkeypatch):
    fixture_assets(monkeypatch)
    notebook._prebuilt_runtime(tmp_path)
    (tmp_path / "runtime.tar.gz").write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="checksum"):
        notebook._prebuilt_runtime(tmp_path)


def test_runtime_tar_cannot_escape_destination(tmp_path, monkeypatch):
    fixture_assets(monkeypatch, unsafe=True)
    with pytest.raises(tarfile.FilterError):
        notebook._prebuilt_runtime(tmp_path)
    assert not (tmp_path / "escape").exists()


def test_source_build_timeout_stops_owned_process_tree(tmp_path, monkeypatch):
    class Process:
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("cmake", timeout)

    process = Process()
    stopped = []
    monkeypatch.setattr(notebook.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(notebook, "stop_tree", stopped.append)
    with pytest.raises(RuntimeError, match="child processes"):
        notebook._run_build(["cmake"], timeout=1, log=tmp_path / "build.log")
    assert stopped == [process]


def test_cpu_fallback_is_rejected_before_loading_weights(monkeypatch):
    monkeypatch.setattr(
        notebook.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="Available devices:\n(none)", stderr=""
        ),
    )
    with pytest.raises(RuntimeError, match="CPU fallback"):
        notebook._require_cuda("llama-server", {})
    monkeypatch.setattr(
        notebook.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="CUDA0: Tesla T4", stderr=""),
    )
    notebook._require_cuda("llama-server", {})


def test_setup_rejects_a_duplicate_owned_model_but_not_a_reused_pid(tmp_path, monkeypatch):
    (tmp_path / "runtime.json").write_text(json.dumps({"pid": 42}))
    monkeypatch.setattr(
        notebook,
        "_runtime_command",
        lambda pid: str(tmp_path / "llama-server").encode() + b"\0-m\0model.gguf",
    )
    with pytest.raises(RuntimeError, match="already has a model"):
        notebook._reject_running_model(tmp_path)
    monkeypatch.setattr(notebook, "_runtime_command", lambda pid: b"unrelated-process")
    notebook._reject_running_model(tmp_path)
