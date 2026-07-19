"""
Pytest configuration for GraphRapping tests.

Sub-task 2 (P0-3): with kg_mode now resolved from GRAPHRAPPING_KG_MODE env,
tests must run with a clean env to keep results deterministic regardless of
the developer's shell environment. KG-intent tests set the env explicitly.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_graphrapping_kg_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear ambient GraphRapping input-connector envs before each test.

    Tests that need a specific value either:
    - call monkeypatch.setenv(...) themselves, or
    - pass the value explicitly as a function argument.

    IC-1 (plan §6/codex #7): the input-connector envs are cleared here so a
    developer's shell can never pollute deterministic runs. Note
    ``GRAPHRAPPING_DEMO_REVIEW_PATH`` is import-captured into
    ``server._DEFAULT_REVIEW_PATH``; delenv cannot rewrite that already-captured
    module constant (by design — the capture is intentionally NOT refactored),
    but clearing the env still prevents any call-time reader from seeing an
    ambient value, and in a clean import env the constant is already None.
    """
    monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_USER_PROFILES_JSON", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_REVIEW_TRIPLES_JSON", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_PRODUCT_CATALOG_JSON", raising=False)
    monkeypatch.delenv("GRAPHRAPPING_DEMO_REVIEW_PATH", raising=False)
