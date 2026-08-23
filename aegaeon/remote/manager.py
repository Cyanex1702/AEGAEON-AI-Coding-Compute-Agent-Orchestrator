from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select, update
from websockets.asyncio.client import connect

from aegaeon.config import Settings
from aegaeon.database.models import WorkerNotebookRecord
from aegaeon.database.session import Database
from aegaeon.events import EventBus
from aegaeon.remote.providers import (
    ManualPublicUrlProvider,
    NgrokTunnelProvider,
    TunnelProvider,
    TunnelProviderError,
    cloudflared_installed,
)
from aegaeon.remote.schemas import (
    RemoteConnectionRead,
    RemoteConnectionState,
    TunnelProviderRead,
)
from aegaeon.remote.secrets import RemoteSecretStore

REMOTE_TARGETS = {"google_colab", "kaggle", "cloud_gpu"}


def is_local_controller_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    if hostname in {"localhost", "0.0.0.0", "::", ""}:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return (
        address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified
    )


class RemoteConnectivityManager:
    """Owns provider lifecycle and exposes only verified HTTPS + WSS state."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        events: EventBus,
        *,
        providers: dict[str, TunnelProvider] | None = None,
        secret_store: RemoteSecretStore | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.events = events
        self.providers = providers or {
            "ngrok": NgrokTunnelProvider(),
            "manual": ManualPublicUrlProvider(),
        }
        self.secrets = secret_store or RemoteSecretStore(settings.data_dir / "remote-secrets.json")
        self.state_path = settings.data_dir / "remote-connectivity.json"
        self.probe_secret = secrets.token_urlsafe(32)
        self._provider: TunnelProvider | None = None
        self._lock = asyncio.Lock()
        self._monitor_task: asyncio.Task[None] | None = None
        self._last_request: tuple[str, str | None] | None = None
        self._status = RemoteConnectionRead(
            local_url=self.local_url,
            local_controller=is_local_controller_url(self.local_url),
            updated_at=datetime.now(UTC),
        )

    @property
    def local_url(self) -> str:
        host = self.settings.host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{self.settings.port}"

    @property
    def public_hostname(self) -> str | None:
        return urlsplit(self._status.public_url).hostname if self._status.public_url else None

    def is_public_request_host(self, host_header: str | None) -> bool:
        if not self.public_hostname or not host_header:
            return False
        host = host_header.rsplit(":", 1)[0].strip("[]").lower()
        return host == self.public_hostname.lower()

    def status(self) -> RemoteConnectionRead:
        data = self._status.model_copy(deep=True)
        data.providers = self.provider_statuses()
        data.stale_notebooks = self.stale_notebook_count()
        return data

    def provider_statuses(self) -> list[TunnelProviderRead]:
        ngrok = self.providers.get("ngrok")
        manual = self.providers.get("manual")
        result = [
            TunnelProviderRead(
                id="ngrok",
                name="ngrok",
                installed=bool(ngrok and ngrok.installed),
                token_required=True,
                token_configured=self.secrets.configured("ngrok"),
                automatic=True,
                setup_url=ngrok.setup_url if ngrok else None,
                detail="Recommended automatic HTTPS and WebSocket connection.",
            ),
            TunnelProviderRead(
                id="cloudflare",
                name="Cloudflare Tunnel",
                installed=cloudflared_installed(),
                token_required=False,
                token_configured=False,
                automatic=False,
                setup_url="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/",
                detail="Detected for future named-tunnel support; not used automatically.",
            ),
        ]
        if manual:
            result.append(
                TunnelProviderRead(
                    id="manual",
                    name=manual.name,
                    installed=True,
                    token_required=False,
                    token_configured=False,
                    automatic=False,
                    detail="Advanced: verify an existing HTTPS controller address.",
                )
            )
        return result

    def save_token(self, provider: str, token: str) -> None:
        if provider != "ngrok":
            raise ValueError("That provider does not accept a saved token")
        self.secrets.set(provider, token.strip())

    def remove_token(self, provider: str) -> None:
        self.secrets.remove(provider)

    async def reconcile(self) -> None:
        if not self.state_path.exists():
            return
        try:
            previous = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._status.generation = int(previous.get("generation", 0))
        if previous.get("state") == RemoteConnectionState.READY.value:
            self._status.state = RemoteConnectionState.STOPPED
            self._status.error = (
                "The previous temporary connection ended with the controller. "
                "Reconnect remote workers to create a fresh address."
            )
            self._status.public_url = previous.get("public_url")
            self._mark_stale()
        self._status.updated_at = datetime.now(UTC)

    async def start(
        self, provider_id: str = "auto", public_url: str | None = None
    ) -> RemoteConnectionRead:
        async with self._lock:
            await self._stop_provider()
            selected = "ngrok" if provider_id == "auto" else provider_id
            provider = self.providers.get(selected)
            if provider is None:
                return await self._fail("The selected remote connection provider is unavailable.")
            self._last_request = (provider_id, public_url)
            await self._transition(
                RemoteConnectionState.CHECKING,
                "Checking the local AEGAEON controller",
                "tunnel.setup_started",
            )
            try:
                await self._verify_local()
                if not provider.installed:
                    raise TunnelProviderError(
                        "Remote access component is not installed. Open Settings to install ngrok."
                    )
                token = public_url if selected == "manual" else self.secrets.get(selected)
                if provider.token_required and not token:
                    raise TunnelProviderError(
                        "Connect ngrok in Settings, then select Set Up Automatically again."
                    )
                await self._transition(
                    RemoteConnectionState.STARTING_TUNNEL,
                    f"Starting {provider.name}",
                    "tunnel.provider_selected",
                    {"provider": selected},
                )
                self._provider = provider
                local_host = urlsplit(self.local_url).hostname or "127.0.0.1"
                url = await provider.start(local_host, self.settings.port, token)
                if not url.startswith("https://"):
                    raise TunnelProviderError("Provider did not return a secure HTTPS address.")
                previous = self._status.public_url
                await self._transition(
                    RemoteConnectionState.TESTING_HTTPS,
                    "Secure endpoint created; testing HTTPS",
                    "tunnel.public_url_received",
                    {"provider": selected},
                )
                self._status.public_url = url
                self._status.provider = selected
                latency = await self._verify_https(url)
                self._status.rest_ok = True
                self._status.latency_ms = latency
                await self.events.publish(
                    "tunnel.http_verified",
                    "Remote HTTPS health check passed",
                    payload={"latency_ms": latency},
                )
                await self._transition(
                    RemoteConnectionState.TESTING_WEBSOCKET,
                    "HTTPS connected; testing the worker WebSocket",
                    "tunnel.websocket_testing",
                )
                await self._verify_websocket(url)
                self._status.websocket_ok = True
                self._status.state = RemoteConnectionState.READY
                self._status.error = None
                self._status.reconnect_attempt = 0
                if previous != url:
                    self._status.generation += 1
                    self._status.url_changed = previous is not None
                    if previous:
                        self._mark_stale(url)
                        await self.events.publish(
                            "worker.bootstrap_stale",
                            "Previously generated worker notebooks use an old address",
                            payload={"count": self.stale_notebook_count()},
                        )
                        await self.events.publish(
                            "tunnel.url_changed",
                            "Remote address changed; older worker notebooks are stale",
                            payload={"generation": self._status.generation},
                        )
                self._status.updated_at = datetime.now(UTC)
                self._persist()
                await self.events.publish(
                    "tunnel.websocket_verified",
                    "Remote worker WebSocket handshake passed",
                )
                await self.events.publish(
                    "tunnel.ready",
                    "Remote workers can now connect securely",
                    payload={"provider": selected, "generation": self._status.generation},
                )
                if provider.automatic:
                    self._monitor_task = asyncio.create_task(
                        self._monitor(), name="remote-connectivity"
                    )
                return self.status()
            except Exception as exc:
                await self._stop_provider()
                return await self._fail(str(exc))

    async def restart(self) -> RemoteConnectionRead:
        if not self._last_request:
            return await self.start("auto")
        return await self.start(*self._last_request)

    async def stop(self) -> RemoteConnectionRead:
        async with self._lock:
            await self._stop_provider()
            self._status.state = RemoteConnectionState.STOPPED
            self._status.rest_ok = False
            self._status.websocket_ok = False
            self._status.updated_at = datetime.now(UTC)
            self._mark_stale()
            self._persist()
            await self.events.publish("tunnel.stopped", "Remote connection stopped cleanly")
            return self.status()

    async def shutdown(self) -> None:
        await self._stop_provider()

    def require_remote_url(self) -> tuple[str, int]:
        if (
            self._status.state != RemoteConnectionState.READY
            or not self._status.rest_ok
            or not self._status.websocket_ok
            or not self._status.public_url
        ):
            raise RuntimeError(
                "Remote connection required first. Select Set Up Automatically and wait for "
                "HTTPS and WebSocket checks to pass."
            )
        return self._status.public_url, self._status.generation

    def record_notebook(
        self, artifact_id: str, project_id: str, controller_url: str, generation: int
    ) -> None:
        with self.database.session() as session:
            session.add(
                WorkerNotebookRecord(
                    artifact_id=artifact_id,
                    project_id=project_id,
                    controller_url=controller_url,
                    connection_generation=generation,
                    stale=False,
                )
            )

    def stale_notebook_count(self, project_id: str | None = None) -> int:
        with self.database.session() as session:
            statement = (
                select(func.count())
                .select_from(WorkerNotebookRecord)
                .where(WorkerNotebookRecord.stale.is_(True))
            )
            if project_id:
                statement = statement.where(WorkerNotebookRecord.project_id == project_id)
            return int(session.scalar(statement) or 0)

    async def _verify_local(self) -> None:
        async with httpx.AsyncClient(timeout=4) as client:
            response = await client.get(f"{self.local_url}/health")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "ok" or payload.get("version") != "0.3.0":
            raise RuntimeError("The configured controller port is not this AEGAEON instance.")

    async def _verify_https(self, public_url: str) -> int:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(f"{public_url}/health")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "ok" or payload.get("version") != "0.3.0":
            raise RuntimeError("Public HTTPS route does not point to this AEGAEON controller.")
        return round((time.monotonic() - started) * 1000)

    async def _verify_websocket(self, public_url: str) -> None:
        websocket_url = public_url.replace("https://", "wss://", 1) + "/ws/worker"
        async with connect(
            websocket_url,
            additional_headers={"X-AEGAEON-Probe": self.probe_secret},
            open_timeout=10,
        ) as socket:
            raw = await asyncio.wait_for(socket.recv(), timeout=5)
            value = json.loads(raw)
            if value.get("type") != "probe.ok":
                raise RuntimeError("Remote endpoint did not complete the worker WebSocket probe.")

    async def _monitor(self) -> None:
        try:
            while self._provider and self._status.state == RemoteConnectionState.READY:
                await asyncio.sleep(5)
                if self._provider.running:
                    continue
                for attempt in range(1, 4):
                    self._status.state = RemoteConnectionState.RECONNECTING
                    self._status.reconnect_attempt = attempt
                    self._status.updated_at = datetime.now(UTC)
                    await self.events.publish(
                        "tunnel.reconnecting",
                        f"Remote connection lost; reconnecting (attempt {attempt}/3)",
                        payload={"attempt": attempt},
                    )
                    result = await self.restart()
                    if result.state == RemoteConnectionState.READY:
                        return
                    await asyncio.sleep(min(attempt * 2, 5))
                await self._fail("Remote connection could not be recovered after 3 attempts.")
                return
        except asyncio.CancelledError:
            raise

    async def _stop_provider(self) -> None:
        current = asyncio.current_task()
        if self._monitor_task and self._monitor_task is not current:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        self._monitor_task = None
        if self._provider:
            await self._provider.stop()
        self._provider = None

    async def _transition(
        self,
        state: RemoteConnectionState,
        message: str,
        event_type: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        self._status.state = state
        self._status.error = None
        self._status.updated_at = datetime.now(UTC)
        await self.events.publish(event_type, message, payload=payload)

    async def _fail(self, message: str) -> RemoteConnectionRead:
        self._status.state = RemoteConnectionState.FAILED
        self._status.rest_ok = False
        self._status.websocket_ok = False
        self._status.error = message
        self._status.updated_at = datetime.now(UTC)
        self._persist()
        await self.events.publish("tunnel.failed", message)
        return self.status()

    def _mark_stale(self, active_url: str | None = None) -> None:
        with self.database.session() as session:
            statement = update(WorkerNotebookRecord).where(WorkerNotebookRecord.stale.is_(False))
            if active_url:
                statement = statement.where(WorkerNotebookRecord.controller_url != active_url)
            session.execute(statement.values(stale=True))

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        safe = {
            "state": self._status.state.value,
            "provider": self._status.provider,
            "public_url": self._status.public_url,
            "generation": self._status.generation,
            "updated_at": self._status.updated_at.isoformat(),
        }
        self.state_path.write_text(json.dumps(safe, indent=2), encoding="utf-8")
