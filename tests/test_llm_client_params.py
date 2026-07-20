"""Azure chat-completion parameter ladder (llm_client, 2026-07-20).

Newer Azure deployments (GPT-5.x reasoning class) reject ``max_tokens``
(400: use ``max_completion_tokens``) and non-default ``temperature``; older
chat deployments predate ``max_completion_tokens``. ``complete_json`` must
try the modern body first and fall back to the legacy shapes on 400/404/422.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.rec.llm_client import AzureOpenAIClient


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _FakeHttpx.HTTPStatusError("boom", response=self)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpx:
    class HTTPStatusError(Exception):
        def __init__(self, msg: str, *, response: Any = None):
            super().__init__(msg)
            self.response = response

    def __init__(self, responses: list[_Resp]):
        self._responses = responses
        self.bodies: list[dict[str, Any]] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> _Resp:
        self.bodies.append(json)
        return self._responses[len(self.bodies) - 1]


_OK = _Resp(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})


def _client(fake: _FakeHttpx) -> AzureOpenAIClient:
    return AzureOpenAIClient(
        httpx_mod=fake, endpoint="https://x", api_key="k",
        deployment="d", api_version="v",
    )


def test_modern_body_first_no_max_tokens_no_temperature() -> None:
    fake = _FakeHttpx([_OK])
    assert _client(fake).complete_json("s", "u", timeout_sec=5) == {"ok": True}
    body = fake.bodies[0]
    assert body["max_completion_tokens"] == 800
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert body["response_format"] == {"type": "json_object"}


def test_400_falls_back_to_legacy_max_tokens() -> None:
    fake = _FakeHttpx([_Resp(400), _OK])
    assert _client(fake).complete_json("s", "u", timeout_sec=5) == {"ok": True}
    legacy = fake.bodies[1]
    assert legacy["max_tokens"] == 800
    assert legacy["temperature"] == 0
    assert "max_completion_tokens" not in legacy


def test_second_400_drops_response_format() -> None:
    fake = _FakeHttpx([_Resp(400), _Resp(400), _OK])
    assert _client(fake).complete_json("s", "u", timeout_sec=5) == {"ok": True}
    last = fake.bodies[2]
    assert "response_format" not in last
    assert last["max_tokens"] == 800


def test_non_retryable_status_raises() -> None:
    fake = _FakeHttpx([_Resp(500)])
    with pytest.raises(_FakeHttpx.HTTPStatusError):
        _client(fake).complete_json("s", "u", timeout_sec=5)
    assert len(fake.bodies) == 1


def test_exhausted_ladder_raises_last_error() -> None:
    fake = _FakeHttpx([_Resp(400), _Resp(400), _Resp(422)])
    with pytest.raises(_FakeHttpx.HTTPStatusError):
        _client(fake).complete_json("s", "u", timeout_sec=5)
    assert len(fake.bodies) == 3
