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
    """Clear ambient GRAPHRAPPING_KG_MODE before each test.

    Tests that need a specific kg_mode either:
    - call monkeypatch.setenv("GRAPHRAPPING_KG_MODE", "...") themselves, or
    - pass kg_mode explicitly as a function argument.
    """
    monkeypatch.delenv("GRAPHRAPPING_KG_MODE", raising=False)
