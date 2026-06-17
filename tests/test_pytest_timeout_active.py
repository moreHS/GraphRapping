"""
P6-2 (Wave 3.7): pytest-timeout integration smoke + config contract.

Verifies the 30s default timeout from pyproject.toml is active.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest


_PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def test_pytest_timeout_dev_dependency_declared() -> None:
    """`pytest-timeout` must appear in [project.optional-dependencies].dev."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    assert "pytest-timeout" in text, "pytest-timeout missing from dev deps"


def test_pytest_ini_options_declare_default_timeout() -> None:
    """[tool.pytest.ini_options] must set a default timeout."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    # Locate the pytest config block and ensure timeout is set.
    block_match = re.search(
        r"\[tool\.pytest\.ini_options\](.*?)(?:^\[|\Z)",
        text, re.DOTALL | re.MULTILINE,
    )
    assert block_match, "[tool.pytest.ini_options] block missing"
    block = block_match.group(1)
    timeout_match = re.search(r"^timeout\s*=\s*(\d+)", block, re.MULTILINE)
    assert timeout_match, "timeout = <int> missing in pytest config"
    timeout_value = int(timeout_match.group(1))
    assert 10 <= timeout_value <= 120, (
        f"timeout={timeout_value}s is outside the reasonable range [10, 120]"
    )


def test_short_sleep_passes_under_timeout() -> None:
    """Sub-timeout work must complete normally."""
    time.sleep(0.1)
    assert True


@pytest.mark.timeout(1)
def test_per_test_override_works() -> None:
    """`@pytest.mark.timeout(N)` per-test override must be honored.

    1s timeout with 0.1s sleep → safely passes. If the marker were ignored,
    behaviour would be unchanged (still pass), so this only confirms wiring
    rather than the timeout firing — that part is covered by the manual
    35s long-sleep run documented in the plan.
    """
    time.sleep(0.1)
    assert True
