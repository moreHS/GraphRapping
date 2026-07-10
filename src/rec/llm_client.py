"""
LLM client abstraction for query understanding (Phase 6 Track B, B1).

The query-understanding module (``src.rec.query_understanding``) uses an LLM
ONLY as a natural-language → dictionary-vocabulary translator. This module
provides the provider-agnostic seam it calls through:

- ``LLMClient`` — a minimal Protocol: ``complete_json(system, user, *,
  timeout_sec) -> dict``.
- ``AzureOpenAIClient`` / ``AnthropicClient`` — direct REST clients (no vendor
  SDK dependency) built on ``httpx``.
- ``build_llm_client`` — resolves ``GRAPHRAPPING_QUERY_LLM`` + the provider env
  vars into a client, or ``None`` (→ the caller uses its dictionary fallback).

Design constraints:
- ``httpx`` is an OPTIONAL dependency (pyproject extra ``query-llm``). It is
  imported lazily and guarded: if it is not installed, ``build_llm_client``
  logs a warning and returns ``None`` (never raises ImportError). The core
  demo/server therefore runs with no query-LLM dependency; the dictionary
  fallback path is always available.
- API keys are read from the environment only and are NEVER logged. Warnings
  mention env-var *names*, never values.
- The API response is expected to be a JSON object. Both providers are asked
  for JSON-only output and the content is parsed leniently (code fences
  stripped, first ``{...}`` span extracted) so a deployment that does not
  honor a structured-output flag still works.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """A provider that turns a (system, user) prompt into a JSON object.

    Implementations must return a parsed ``dict``; any transport/parse failure
    should raise (the caller catches everything and falls back to dictionary
    resolution).
    """

    def complete_json(self, system: str, user: str, *, timeout_sec: float) -> dict[str, Any]:
        ...


# ---------------------------------------------------------------------------
# Optional httpx guard
# ---------------------------------------------------------------------------

def _get_httpx() -> Any | None:
    """Return the ``httpx`` module, or ``None`` if it is not installed.

    Imported lazily (not at module top) so that the query-LLM feature is a
    genuinely optional dependency: importing this module, and running the whole
    test suite / demo, must not require ``httpx``. Kept as a call-time ``import``
    (rather than caching a module-level reference) so a missing install is
    detected at the moment a client is actually requested.
    """
    try:
        import httpx
    except ImportError:
        logger.warning(
            "httpx is not installed; query LLM disabled (install the 'query-llm' extra). "
            "Falling back to dictionary resolution."
        )
        return None
    return httpx


# ---------------------------------------------------------------------------
# JSON parsing helpers (shared by both providers)
# ---------------------------------------------------------------------------

_FENCE_PREFIX_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?")
_FENCE_SUFFIX_RE = re.compile(r"\n?\s*```$")


def _parse_json_lenient(text: str) -> dict[str, Any]:
    """Parse an LLM text response into a JSON object, tolerating wrappers.

    Handles the common ways a chat model emits JSON despite a "JSON only"
    instruction: leading/trailing prose is stripped by falling back to the
    first ``{ ... }`` span, and Markdown code fences (```json ... ```) are
    removed. Raises ``ValueError`` if no JSON object can be recovered.
    """
    if not text or not text.strip():
        raise ValueError("empty LLM content")

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_PREFIX_RE.sub("", stripped)
        stripped = _FENCE_SUFFIX_RE.sub("", stripped).strip()

    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM content is not valid JSON")
        obj = json.loads(stripped[start : end + 1])

    if not isinstance(obj, dict):
        raise ValueError("LLM content is not a JSON object")
    return obj


def _extract_anthropic_text(data: Any) -> str:
    """Pull the first text block out of an Anthropic Messages API response."""
    content = data.get("content") if isinstance(data, dict) else None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return text
    raise ValueError("Anthropic response contained no text content")


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class AzureOpenAIClient:
    """Azure OpenAI chat-completions client (direct REST via httpx).

    Attempts ``response_format=json_object``; if the deployment/API version
    rejects it (4xx), retries once without it, relying on the prompt's
    JSON-only instruction + lenient parsing.
    """

    def __init__(
        self,
        httpx_mod: Any,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str,
    ) -> None:
        self._httpx = httpx_mod
        base = endpoint.rstrip("/")
        self._url = f"{base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        self._headers = {"api-key": api_key, "content-type": "application/json"}

    def complete_json(self, system: str, user: str, *, timeout_sec: float) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 800,
            "response_format": {"type": "json_object"},
        }
        try:
            return self._post(body, timeout_sec)
        except self._httpx.HTTPStatusError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (400, 404, 422):
                # Deployment likely does not support response_format — retry
                # without it (prompt still instructs JSON-only).
                body.pop("response_format", None)
                return self._post(body, timeout_sec)
            raise

    def _post(self, body: dict[str, Any], timeout_sec: float) -> dict[str, Any]:
        resp = self._httpx.post(self._url, headers=self._headers, json=body, timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("unexpected Azure OpenAI response shape") from exc
        if not isinstance(content, str):
            raise ValueError("Azure OpenAI response content is not a string")
        return _parse_json_lenient(content)


class AnthropicClient:
    """Anthropic Messages API client (direct REST via httpx; no SDK).

    Model defaults to ``claude-haiku-4-5`` — a fast, low-cost model well suited
    to short structured extraction. The Anthropic API has no OpenAI-style
    ``response_format`` flag, so JSON is prompt-driven + leniently parsed.
    """

    _URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, httpx_mod: Any, api_key: str, model: str = "claude-haiku-4-5") -> None:
        self._httpx = httpx_mod
        self._model = model
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def complete_json(self, system: str, user: str, *, timeout_sec: float) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        resp = self._httpx.post(self._URL, headers=self._headers, json=body, timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        return _parse_json_lenient(_extract_anthropic_text(data))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_llm_client(provider: str | None = None) -> LLMClient | None:
    """Build a query-understanding LLM client, or ``None`` for the fallback.

    Provider selection (``provider`` arg overrides the ``GRAPHRAPPING_QUERY_LLM``
    env var):
    - unset / ``""`` / ``off`` → ``None`` (dictionary fallback). Leaving the env
      unset is a deliberate "auto-off" default so the demo never tries to reach
      a provider with no credentials.
    - ``azure`` → ``AzureOpenAIClient`` (the confirmed default provider when the
      feature is enabled), requires the four ``AZURE_OPENAI_*`` env vars.
    - ``anthropic`` → ``AnthropicClient``, requires ``ANTHROPIC_API_KEY``.

    Returns ``None`` (with a warning) when httpx is missing or credentials are
    not configured — never raises.
    """
    resolved = (provider if provider is not None else os.environ.get("GRAPHRAPPING_QUERY_LLM", ""))
    resolved = resolved.strip().lower()

    if resolved in ("", "off"):
        return None
    if resolved not in ("azure", "anthropic"):
        logger.warning(
            "Unknown GRAPHRAPPING_QUERY_LLM=%r; disabling query LLM (using dictionary fallback).",
            resolved,
        )
        return None

    httpx_mod = _get_httpx()
    if httpx_mod is None:
        return None

    if resolved == "azure":
        return _build_azure(httpx_mod)
    return _build_anthropic(httpx_mod)


def _build_azure(httpx_mod: Any) -> LLMClient | None:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "").strip()
    missing = [
        name
        for name, value in (
            ("AZURE_OPENAI_ENDPOINT", endpoint),
            ("AZURE_OPENAI_API_KEY", api_key),
            ("AZURE_OPENAI_DEPLOYMENT", deployment),
            ("AZURE_OPENAI_API_VERSION", api_version),
        )
        if not value
    ]
    if missing:
        logger.warning(
            "Azure OpenAI query LLM not configured (missing %s); using dictionary fallback.",
            ", ".join(missing),
        )
        return None
    return AzureOpenAIClient(httpx_mod, endpoint, api_key, deployment, api_version)


def _build_anthropic(httpx_mod: Any) -> LLMClient | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "Anthropic query LLM not configured (missing ANTHROPIC_API_KEY); using dictionary fallback."
        )
        return None
    return AnthropicClient(httpx_mod, api_key)
