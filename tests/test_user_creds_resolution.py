"""IC-3 user-connector credential migration (plan 2026-07-20 §3).

Credential resolution order (env-first, personal-agent .env fallback) and the
initial-load K=100 default. No live DB — the resolver is pure over an injected
``environ`` mapping and a file path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.fetch_user_profiles_pg import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    _limit_type,
    resolve_db_credentials,
)

_FULL_ENV = {
    "AIBE_DB_URL": "host", "AIBE_DB_NM": "db",
    "AIBE_DB_USER": "u", "AIBE_DB_PW": "pw",
}


def test_environ_wins_when_all_required_present(tmp_path: Path) -> None:
    creds, source = resolve_db_credentials(tmp_path / "nonexistent.env", environ=_FULL_ENV)
    assert source == "environ"
    assert creds["AIBE_DB_URL"] == "host"  # from environ, file never read


def test_environ_carries_optional_keys(tmp_path: Path) -> None:
    env = dict(_FULL_ENV, AIBE_DB_PORT="6543", AIBE_DB_SCHEMA="agent")
    creds, source = resolve_db_credentials(tmp_path / "x.env", environ=env)
    assert source == "environ"
    assert creds["AIBE_DB_PORT"] == "6543"
    assert creds["AIBE_DB_SCHEMA"] == "agent"


def test_falls_back_to_env_file_when_environ_incomplete(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AIBE_DB_URL=fhost\nAIBE_DB_NM=fdb\nAIBE_DB_USER=fu\nAIBE_DB_PW=fpw\n",
        encoding="utf-8",
    )
    partial = {"AIBE_DB_URL": "host", "AIBE_DB_NM": "db", "AIBE_DB_USER": "u"}  # PW missing
    creds, source = resolve_db_credentials(env_file, environ=partial)
    assert source == "env_file(fallback)"
    assert creds["AIBE_DB_URL"] == "fhost"  # from the fallback file


def test_fallback_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_db_credentials(tmp_path / "nope.env", environ={})


# ---------------------------------------------------------------------------
# Initial load K = 100
# ---------------------------------------------------------------------------

def test_default_limit_is_100_within_cap() -> None:
    assert DEFAULT_LIMIT == 100
    assert DEFAULT_LIMIT <= MAX_LIMIT


def test_limit_type_accepts_100() -> None:
    assert _limit_type("100") == 100
