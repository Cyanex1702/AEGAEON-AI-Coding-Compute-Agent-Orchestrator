from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
from abc import ABC, abstractmethod
from typing import Any

import httpx


class TunnelProviderError(RuntimeError):
    pass


class TunnelProvider(ABC):
    id: str
    name: str
    token_required: bool = False
    automatic: bool = True
    setup_url: str | None = None

    @property
    @abstractmethod
    def installed(self) -> bool: ...

    @abstractmethod
    async def start(self, local_host: str, local_port: int, token: str | None) -> str: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @property
    @abstractmethod
    def running(self) -> bool: ...

    @property
    def requires_remote_health_check(self) -> bool:
        return False


class NgrokTunnelProvider(TunnelProvider):
    id = "ngrok"
    name = "ngrok"
    token_required = True
    setup_url = "https://dashboard.ngrok.com/get-started/your-authtoken"

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self._tunnel_name: str | None = None
        self._target: str | None = None
        self._adopted_url: str | None = None

    @property
    def installed(self) -> bool:
        return shutil.which("ngrok") is not None

    @property
    def running(self) -> bool:
        return self._adopted_url is not None or (
            self.process is not None and self.process.returncode is None
        )

    @property
    def requires_remote_health_check(self) -> bool:
        return self._adopted_url is not None

    async def start(self, local_host: str, local_port: int, token: str | None) -> str:
        executable = shutil.which("ngrok")
        if not executable:
            raise TunnelProviderError(
                "Remote access component is not installed. Install ngrok from Settings."
            )
        if not token:
            raise TunnelProviderError("Connect ngrok in Settings before automatic setup.")
        await self.stop()
        self._target = f"{local_host}:{local_port}"
        existing = await self._existing_aegaeon_tunnel()
        if existing:
            self._adopted_url = existing
            return existing

        self._tunnel_name = f"aegaeon-{secrets.token_hex(8)}"
        environment = os.environ.copy()
        environment["NGROK_AUTHTOKEN"] = token
        self.process = await asyncio.create_subprocess_exec(
            executable,
            "http",
            f"{local_host}:{local_port}",
            "--name",
            self._tunnel_name,
            "--log",
            "false",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(30):
                if self.process.returncode is not None:
                    output = ""
                    if self.process.stdout:
                        output = (await self.process.stdout.read()).decode(errors="replace")
                    raise TunnelProviderError(self._stopped_error(output))
                try:
                    response = await client.get("http://127.0.0.1:4040/api/tunnels")
                    response.raise_for_status()
                    tunnels: list[dict[str, Any]] = response.json().get("tunnels", [])
                    url = self._owned_https_url(tunnels)
                    if url:
                        return url.rstrip("/")
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(0.5)
        await self.stop()
        raise TunnelProviderError("ngrok did not return a secure public URL in time.")

    async def _existing_aegaeon_tunnel(self) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=1) as client:
                response = await client.get("http://127.0.0.1:4040/api/tunnels")
                response.raise_for_status()
                tunnels: list[dict[str, Any]] = response.json().get("tunnels", [])
        except (httpx.HTTPError, ValueError):
            return None
        adopted = self._adoptable_https_tunnel(tunnels)
        if adopted:
            self._tunnel_name, public_url = adopted
            return public_url
        return None

    def _adoptable_https_tunnel(self, tunnels: list[dict[str, Any]]) -> tuple[str, str] | None:
        valid_addresses = {
            self._target,
            f"http://{self._target}",
            f"https://{self._target}",
        }
        return next(
            (
                (str(item.get("name")), str(item.get("public_url")).rstrip("/"))
                for item in tunnels
                if str(item.get("name", "")).startswith("aegaeon-")
                and str(item.get("proto", "")).lower() == "https"
                and str(item.get("public_url", "")).startswith("https://")
                and str(item.get("config", {}).get("addr", "")).rstrip("/") in valid_addresses
            ),
            None,
        )

    @staticmethod
    def _stopped_error(output: str) -> str:
        code = next(iter(re.findall(r"ERR_NGROK_\d+", output)), None)
        if code == "ERR_NGROK_334":
            return (
                "An existing AEGAEON ngrok endpoint is already online. "
                "Reconnect again to adopt it, or stop the stale ngrok process."
            )
        if code in {"ERR_NGROK_105", "ERR_NGROK_106"}:
            return f"ngrok rejected the saved credential ({code}). Reconnect ngrok in Settings."
        suffix = f" ({code})" if code else ""
        return f"ngrok stopped before creating a public connection{suffix}."

    def _owned_https_url(self, tunnels: list[dict[str, Any]]) -> str | None:
        valid_addresses = {
            self._target,
            f"http://{self._target}",
            f"https://{self._target}",
        }
        return next(
            (
                str(item.get("public_url")).rstrip("/")
                for item in tunnels
                if item.get("name") == self._tunnel_name
                and str(item.get("proto", "")).lower() == "https"
                and str(item.get("public_url", "")).startswith("https://")
                and str(item.get("config", {}).get("addr", "")).rstrip("/") in valid_addresses
            ),
            None,
        )

    async def stop(self) -> None:
        if self.process is None:
            self._adopted_url = None
            self._tunnel_name = None
            self._target = None
            return
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        self._tunnel_name = None
        self._target = None
        self._adopted_url = None


class ManualPublicUrlProvider(TunnelProvider):
    id = "manual"
    name = "Existing public URL"
    automatic = False

    def __init__(self) -> None:
        self._url: str | None = None

    @property
    def installed(self) -> bool:
        return True

    @property
    def running(self) -> bool:
        return self._url is not None

    async def start(self, local_host: str, local_port: int, token: str | None) -> str:
        if not token or not token.startswith("https://"):
            raise TunnelProviderError("Enter a valid HTTPS controller URL.")
        self._url = token.rstrip("/")
        return self._url

    async def stop(self) -> None:
        self._url = None


def cloudflared_installed() -> bool:
    return shutil.which("cloudflared") is not None
