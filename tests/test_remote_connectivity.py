from __future__ import annotations

from collections.abc import Iterator

import pytest

from aegaeon.config import Settings
from aegaeon.database.session import Database
from aegaeon.events import EventBus
from aegaeon.protocol.schemas import (
    ModelGeneratePayload,
    ModelGenerationContext,
)
from aegaeon.remote.manager import RemoteConnectivityManager, is_local_controller_url
from aegaeon.remote.providers import TunnelProvider
from aegaeon.remote.schemas import RemoteConnectionState
from worker.model_runtime import TransformersRuntime


class FakeProvider(TunnelProvider):
    id = "ngrok"
    name = "fake ngrok"
    token_required = False

    def __init__(self, urls: list[str], *, installed: bool = True) -> None:
        self.urls = iter(urls)
        self.available = installed
        self.started_with: tuple[str, int] | None = None
        self.stopped = 0
        self._running = False

    @property
    def installed(self) -> bool:
        return self.available

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, local_host: str, local_port: int, token: str | None) -> str:
        self.started_with = (local_host, local_port)
        self._running = True
        return next(self.urls)

    async def stop(self) -> None:
        self.stopped += 1
        self._running = False


class VerifiedManager(RemoteConnectivityManager):
    fail_websocket = False

    async def _verify_local(self) -> None:
        return None

    async def _verify_https(self, public_url: str) -> int:
        return 42

    async def _verify_websocket(self, public_url: str) -> None:
        if self.fail_websocket:
            raise RuntimeError("HTTPS works but the WebSocket upgrade failed")


@pytest.fixture
def remote_manager(tmp_path) -> Iterator[tuple[VerifiedManager, FakeProvider, Database]]:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'remote.db'}",
        data_dir=tmp_path / "data",
        worker_token="remote-test-token",
        host="127.0.0.1",
        port=8765,
    )
    database = Database(settings.database_url)
    database.create_all()
    provider = FakeProvider(["https://first.example", "https://second.example"])
    manager = VerifiedManager(
        settings,
        database,
        EventBus(database),
        providers={"ngrok": provider},
    )
    yield manager, provider, database
    database.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://0.0.0.0:8000",
        "http://192.168.1.20:8000",
        "http://10.0.0.2:8000",
    ],
)
def test_remote_targets_detect_local_controller_addresses(url: str) -> None:
    assert is_local_controller_url(url)


def test_public_address_is_not_treated_as_local() -> None:
    assert not is_local_controller_url("https://controller.example")


@pytest.mark.asyncio
async def test_automatic_provider_uses_actual_controller_port_and_verifies_both_layers(
    remote_manager,
) -> None:
    manager, provider, _ = remote_manager
    status = await manager.start("auto")
    assert status.state == RemoteConnectionState.READY
    assert status.rest_ok is True
    assert status.websocket_ok is True
    assert status.latency_ms == 42
    assert status.public_url == "https://first.example"
    assert provider.started_with == ("127.0.0.1", 8765)
    await manager.shutdown()
    assert provider.stopped >= 1


@pytest.mark.asyncio
async def test_provider_unavailable_is_a_friendly_failed_state(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'missing.db'}",
        data_dir=tmp_path / "data",
        worker_token="missing-provider-token",
    )
    database = Database(settings.database_url)
    database.create_all()
    provider = FakeProvider(["https://unused.example"], installed=False)
    manager = VerifiedManager(
        settings, database, EventBus(database), providers={"ngrok": provider}
    )
    status = await manager.start("auto")
    assert status.state == RemoteConnectionState.FAILED
    assert "not installed" in (status.error or "").lower()
    database.close()


@pytest.mark.asyncio
async def test_https_success_does_not_hide_websocket_failure(remote_manager) -> None:
    manager, _, _ = remote_manager
    manager.fail_websocket = True
    status = await manager.start("auto")
    assert status.state == RemoteConnectionState.FAILED
    assert "websocket" in (status.error or "").lower()
    assert status.websocket_ok is False


@pytest.mark.asyncio
async def test_url_rotation_marks_old_notebooks_stale(remote_manager) -> None:
    manager, _, _ = remote_manager
    first = await manager.start("auto")
    manager.record_notebook("artifact-one", "project-one", first.public_url or "", first.generation)
    second = await manager.start("auto")
    assert second.state == RemoteConnectionState.READY
    assert second.url_changed is True
    assert second.generation == first.generation + 1
    assert manager.stale_notebook_count("project-one") == 1
    await manager.shutdown()


class RepairRuntime(TransformersRuntime):
    def __init__(self) -> None:
        super().__init__("fake/model")
        self.model = object()
        self.tokenizer = object()
        self.calls = 0
        self.message_counts: list[int] = []

    def _generate_text(self, messages: list[dict[str, str]], maximum_new_tokens: int) -> str:
        self.calls += 1
        self.message_counts.append(len(messages))
        if self.calls == 1:
            return "not-json"
        return (
            '{"summary":"fixed","files":[{"path":"app.py","content":"print(1)"}],'
            '"test_commands":[],"notes":[]}'
        )


def test_structured_output_repair_is_bounded_and_uses_correction_context() -> None:
    runtime = RepairRuntime()
    payload = ModelGeneratePayload(
        role="coder",
        instructions="Create one safe file",
        context=ModelGenerationContext(
            project_summary="Test repair",
            repository="",
        ),
    )
    result = runtime.generate(payload)
    assert result.summary == "fixed"
    assert runtime.calls == 2
    assert runtime.message_counts == [2, 4]


class AlwaysInvalidRuntime(RepairRuntime):
    def _generate_text(self, messages: list[dict[str, str]], maximum_new_tokens: int) -> str:
        self.calls += 1
        return "still not json"


def test_structured_output_repair_stops_after_three_attempts() -> None:
    runtime = AlwaysInvalidRuntime()
    payload = ModelGeneratePayload(
        role="coder",
        instructions="Create one safe file",
        context=ModelGenerationContext(project_summary="Test repair", repository=""),
    )
    with pytest.raises(ValueError, match="after 3 attempts"):
        runtime.generate(payload)
    assert runtime.calls == 3


def test_public_tunnel_host_only_exposes_worker_surface(client) -> None:
    client.app.state.remote._status.public_url = "https://public.example"
    assert client.get("/health", headers={"host": "public.example"}).status_code == 200
    blocked = client.get("/projects", headers={"host": "public.example"})
    assert blocked.status_code == 403
    assert client.get("/projects", headers={"host": "127.0.0.1:8000"}).status_code == 200


def test_pairing_endpoint_rate_limits_repeated_public_attempts(client) -> None:
    headers = {"x-forwarded-for": "203.0.113.50"}
    for _ in range(10):
        assert (
            client.post(
                "/pairing/redeem",
                json={"pairing_code": "AG-XXXX-XXXX"},
                headers=headers,
            ).status_code
            == 400
        )
    limited = client.post(
        "/pairing/redeem",
        json={"pairing_code": "AG-XXXX-XXXX"},
        headers=headers,
    )
    assert limited.status_code == 429


def test_scoped_pairing_credential_can_use_header_and_be_revoked(client, project_payload) -> None:
    project = client.post("/projects", json=project_payload).json()
    pairing = client.app.state.pairing
    code, _ = pairing.create(project["id"], "remote-worker", "Qwen/model")
    credential = client.post("/pairing/redeem", json={"pairing_code": code}).json()
    assert pairing.identity(credential["worker_token"]) == (
        credential["worker_id"],
        "Qwen/model",
    )
    with client.websocket_connect(
        "/ws/worker",
        headers={"x-aegaeon-worker-token": credential["worker_token"]},
    ) as socket:
        socket.send_json(
            {
                "type": "worker.register",
                "worker_id": credential["worker_id"],
                "hostname": "remote-worker",
                "hardware": {"cpu_cores": 2, "ram_mb": 4096, "gpu": {}},
                "capabilities": ["model.generate"],
                "models": [{"id": "Qwen/model", "loaded": True, "status": "ready"}],
            }
        )
        assert socket.receive_json()["type"] == "worker.registered"
    assert pairing.revoke_worker(credential["worker_id"]) is True
    assert pairing.identity(credential["worker_token"]) is None
