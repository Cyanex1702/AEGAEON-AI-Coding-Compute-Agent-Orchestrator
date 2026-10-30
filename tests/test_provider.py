from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from aegaeon_lite.provider import ModelProvider


def test_native_thinking_is_forwarded_only_when_selected(monkeypatch):
    payloads = []

    def post(self, route, payload, **kwargs):
        if route.endswith("input_tokens"):
            return {"input_tokens": 10}
        payloads.append(payload)
        return {"choices": [{"message": {"content": '{}'}, "finish_reason": "stop"}]}

    monkeypatch.setattr(ModelProvider, "_post", post)
    ModelProvider().complete([])
    ModelProvider(thinking=True).complete([])
    assert payloads[0]["chat_template_kwargs"]["enable_thinking"] is False
    assert payloads[1]["chat_template_kwargs"]["enable_thinking"] is True


@pytest.fixture
def endpoint():
    class Handler(BaseHTTPRequestHandler):
        finish = "stop"
        count = 200

        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("input_tokens"):
                data = {"input_tokens": self.count}
            else:
                assert payload["stream"] is False and payload["max_tokens"] == 2048
                data = {
                    "choices": [
                        {
                            "finish_reason": self.finish,
                            "message": {
                                "content": '{"action":"finish","summary":"Hi","unresolved":[]}'
                            },
                        }
                    ]
                }
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", Handler
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_compatible_endpoint(endpoint):
    url, _ = endpoint
    value = ModelProvider(base_url=url).complete([{"role": "user", "content": "Hi"}])
    assert json.loads(value)["action"] == "finish"


def test_truncated_generation_is_not_executed(endpoint):
    url, handler = endpoint
    handler.finish = "length"
    with pytest.raises(ValueError, match="truncated"):
        ModelProvider(base_url=url).complete([{"role": "user", "content": "Hi"}])


def test_exact_token_budget_enforced(endpoint):
    url, handler = endpoint
    handler.count = 100_000
    with pytest.raises(ValueError, match="context budget"):
        ModelProvider(base_url=url).complete([{"role": "user", "content": "Hi"}])


def test_remote_endpoint_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="permit-remote"):
        ModelProvider(base_url="https://example.com/v1")
    with pytest.raises(ValueError, match="HTTPS"):
        ModelProvider(base_url="http://example.com/v1", permit_remote=True)


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        {},
        "http://localhost:wrong/v1",
        "http://localhost:0/v1",
        "http://localhost:65536/v1",
        "http://localhost:8080/v1?key=value",
    ],
)
def test_malformed_endpoint_is_rejected_before_network(endpoint):
    with pytest.raises(ValueError):
        ModelProvider(base_url=endpoint)


@pytest.mark.parametrize("alias", [{}, [], None, "", "   ", "x" * 201])
def test_model_alias_must_be_bounded_text(alias):
    with pytest.raises(ValueError, match="alias"):
        ModelProvider(model=alias)
