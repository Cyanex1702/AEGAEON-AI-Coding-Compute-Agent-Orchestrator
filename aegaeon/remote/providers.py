from __future__ import annotations

import asyncio
import os
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


class NgrokTunnelProvider(TunnelProvider):
    id = "ngrok"
    name = "ngrok"
    token_required = True
    setup_url = "https://dashboard.ngrok.com/get-started/your-authtoken"

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None

    @property
    def installed(self) -> bool:
        return shutil.which("ngrok") is not None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self, local_host: str, local_port: int, token: str | None) -> str:
        executable = shutil.which("ngrok")
        if not executable:
            raise TunnelProviderError(
                "Remote access component is not installed. Install ngrok from Settings."
            )
        if not token:
            raise TunnelProviderError("Connect ngrok in Settings before automatic setup.")
        await self.stop()
        environment = os.environ.copy()
        environment["NGROK_AUTHTOKEN"] = token
        self.process = await asyncio.create_subprocess_exec(
            executable,
            "http",
            f"{local_host}:{local_port}",
            "--log",
            "false",
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(30):
                if self.process.returncode is not None:
                    raise TunnelProviderError(
                        "ngrok stopped before creating a public connection. Check the saved token."
                    )
                try:
                    response = await client.get("http://127.0.0.1:4040/api/tunnels")
                    response.raise_for_status()
                    tunnels: list[dict[str, Any]] = response.json().get("tunnels", [])
                    url = next(
                        (
                            str(item.get("public_url"))
                            for item in tunnels
                            if str(item.get("public_url", "")).startswith("https://")
                        ),
                        None,
                    )
                    if url:
                        return url.rstrip("/")
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(0.5)
        await self.stop()
        raise TunnelProviderError("ngrok did not return a secure public URL in time.")

    async def stop(self) -> None:
        if self.process is None:
            return
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None


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
