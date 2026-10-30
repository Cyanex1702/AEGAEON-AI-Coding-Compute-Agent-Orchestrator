from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class ModelProvider:
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "local"
    context_tokens: int = 8192
    output_tokens: int = 2048
    timeout: int = 180
    api_key: str = ""
    permit_remote: bool = False
    thinking: bool = False

    def __post_init__(self) -> None:
        if type(self.thinking) is not bool:
            raise ValueError("thinking must be boolean")
        if not isinstance(self.base_url, str) or not 1 <= len(self.base_url) <= 2048:
            raise ValueError("model endpoint must be a URL string")
        url = urllib.parse.urlparse(self.base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("invalid model endpoint")
        try:
            port = url.port
        except ValueError:
            raise ValueError("model endpoint has an invalid port") from None
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("model endpoint has an invalid port")
        if url.query or url.fragment:
            raise ValueError("model endpoint must not contain a query or fragment")
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 200:
            raise ValueError("model alias must be a nonempty string of at most 200 characters")
        if not self.permit_remote and url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote model endpoints need --permit-remote")
        if (
            self.permit_remote
            and url.scheme != "https"
            and url.hostname not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("remote model endpoints require HTTPS")
        if type(self.context_tokens) is not int or not 1024 <= self.context_tokens <= 32768:
            raise ValueError("context must be 1024..32768 tokens")
        if not 128 <= self.output_tokens < self.context_tokens - 512:
            raise ValueError("output budget must leave room for input")
        self.base_url = self.base_url.rstrip("/")

    def _post(self, route: str, payload: dict, timeout: int | None = None) -> dict:
        request = urllib.request.Request(
            self.base_url + route,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + (self.api_key or "no-key"),
            },
        )

        # Redirects must not forward requests or credentials to a different endpoint.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(
                request, timeout=min(timeout or self.timeout, self.timeout)
            ) as response:
                data = response.read(1_000_001)
                if len(data) > 1_000_000:
                    raise ValueError("model response exceeds limit")
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError("model endpoint returned a non-object response")
                return value
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"model endpoint returned HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise RuntimeError("model endpoint unavailable; start llama-server first") from None

    def complete(self, messages: list[dict]) -> str:
        budget = self.context_tokens - self.output_tokens - 256
        try:
            count = self._post(
                "/chat/completions/input_tokens",
                {
                    "model": self.model,
                    "messages": messages,
                },
                timeout=10,
            ).get("input_tokens")
            if not isinstance(count, int) or count < 0:
                raise ValueError("missing token count")
        except (RuntimeError, ValueError):
            # Conservative byte budget for endpoints without the llama.cpp counter.
            # Still only an estimate; the endpoint may reject unsupported contexts.
            count = len(json.dumps(messages, ensure_ascii=False).encode()) + 128
        if count > budget:
            raise ValueError("context budget exceeded; shorten the request or retrieved code")
        reply = self._post(
            "/chat/completions",
            {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.output_tokens,
                "temperature": 0.2,
                "stream": False,
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": self.thinking},
            },
        )
        try:
            choice = reply["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("model output truncated; no action was executed")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content:
                raise ValueError("empty model output")
            return content
        except (KeyError, IndexError, TypeError):
            raise ValueError("model response missing text content") from None
