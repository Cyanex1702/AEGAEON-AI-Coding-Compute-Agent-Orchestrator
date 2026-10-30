import hashlib
import io

import pytest

from aegaeon_lite.models import Models, advice


def manager(tmp_path):
    service = Models(tmp_path)
    data = b"GGUF-test-fixture"
    service.catalog = [
        {
            "id": "tiny",
            "repo": "Qwen/test",
            "revision": "abc123",
            "file": "tiny.gguf",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    ]
    return service, data


def test_verified_atomic_download_and_command(tmp_path, monkeypatch):
    service, data = manager(tmp_path)
    urls = []

    def fetch(url, **kwargs):
        urls.append(url)
        return io.BytesIO(data)

    monkeypatch.setattr("urllib.request.urlopen", fetch)
    service.download(service.item("tiny"))
    assert (service.directory / "tiny.gguf").read_bytes() == data
    assert service.state["status"] == "downloaded"
    assert urls == ["https://huggingface.co/Qwen/test/resolve/abc123/tiny.gguf"]
    assert "No command was executed" in service.command("tiny")["notice"]
    (service.directory / "tiny.gguf").write_bytes(b"x" * len(data))
    with pytest.raises(ValueError, match="checksum"):
        service.command("tiny")


def test_bad_download_preserves_prior_file_and_removes_partial(tmp_path, monkeypatch):
    service, data = manager(tmp_path)
    (service.directory / "tiny.gguf").write_bytes(data)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(b"bad"))
    service.download(service.item("tiny"))
    assert service.state["status"] == "failed"
    assert (service.directory / "tiny.gguf").read_bytes() == data
    assert not (service.directory / "tiny.partial").exists()


def test_cancel_does_not_install_partial(tmp_path, monkeypatch):
    service, data = manager(tmp_path)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(data))
    service.cancel_event.set()
    service.download(service.item("tiny"))
    assert service.state["status"] == "cancelled"
    assert not (service.directory / "tiny.gguf").exists()


def test_catalog_ids_cannot_be_urls_or_paths(tmp_path):
    service, _ = manager(tmp_path)
    for name in ("../tiny", "https://example.com/model", "", None):
        with pytest.raises(ValueError, match="Unknown"):
            service.start(name)


def test_fit_advice_is_conservative_and_not_a_quality_claim():
    model = {"bytes": 1024**3}
    assert advice(model, {"available_ram_bytes": 4 * 1024**3})["fit"] == "may_fit"
    assert advice(model, {"available_ram_bytes": 1024})["fit"] == "tight"
    assert advice(model, {})["fit"] == "unknown"
    assert "does not establish" in advice(model, {})["notice"]


def test_catalog_is_pinned_to_official_assets(tmp_path):
    service = Models(tmp_path)
    assert len(service.catalog) == 4
    for model in service.catalog:
        assert model["repo"].startswith("Qwen/")
        assert len(model["revision"]) == 40 and len(model["sha256"]) == 64
        assert "/" not in model["file"] and model["file"].endswith(".gguf")
