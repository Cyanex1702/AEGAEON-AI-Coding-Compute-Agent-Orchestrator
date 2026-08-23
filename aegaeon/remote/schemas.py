from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RemoteConnectionState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CHECKING = "checking"
    STARTING_TUNNEL = "starting_tunnel"
    WAITING_PROVIDER = "waiting_provider"
    TESTING_HTTPS = "testing_https"
    TESTING_WEBSOCKET = "testing_websocket"
    READY = "ready"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STOPPED = "stopped"


class TunnelSetupRequest(BaseModel):
    provider: str = Field(default="auto", pattern=r"^(auto|ngrok|manual)$")
    public_url: str | None = Field(default=None, pattern=r"^https://")


class RemoteTokenRequest(BaseModel):
    token: str = Field(min_length=8, max_length=512)


class TunnelProviderRead(BaseModel):
    id: str
    name: str
    installed: bool
    token_required: bool
    token_configured: bool
    automatic: bool
    setup_url: str | None = None
    detail: str


class RemoteConnectionRead(BaseModel):
    state: RemoteConnectionState = RemoteConnectionState.NOT_CONFIGURED
    provider: str | None = None
    local_url: str
    local_controller: bool = True
    public_url: str | None = None
    rest_ok: bool = False
    websocket_ok: bool = False
    latency_ms: int | None = None
    error: str | None = None
    generation: int = 0
    url_changed: bool = False
    stale_notebooks: int = 0
    reconnect_attempt: int = 0
    development_warning: str = (
        "Temporary tunnels are intended for development and testing. "
        "Only health, pairing, and authenticated worker connections are public."
    )
    providers: list[TunnelProviderRead] = Field(default_factory=list)
    updated_at: datetime
