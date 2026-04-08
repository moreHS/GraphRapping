#!/usr/bin/env python3
"""Sync user profiles from personal-agent mock_data → GraphRapping mockdata.

Reads MOCK_PROFILES from the personal-agent project, normalizes via
_normalize_profile(), and writes to mockdata/user_profiles_normalized.json.

Usage:
    python scripts/sync_user_profiles.py

Avoids importing the full personal-agent package by using importlib to
load only the needed modules (mock_data.py, data_store.py, date_utils.py).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

# ── Paths ──
GRAPHRAPPING_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = GRAPHRAPPING_ROOT / "mockdata" / "user_profiles_normalized.json"

PERSONAL_AGENT_SRC = Path("/Users/amore/workplace/agent-aibc/persnal-agent/src")
PERSONALIZATION_DIR = PERSONAL_AGENT_SRC / "personalization"


def _load_module(name: str, path: Path) -> types.ModuleType:
    """Load a single .py file as a module without triggering __init__.py."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    # 1. Load date_utils (dependency of data_store)
    date_utils = _load_module(
        "personalization.date_utils",
        PERSONALIZATION_DIR / "date_utils.py",
    )

    # 2. Create a stub personalization package so relative imports work
    pkg = types.ModuleType("personalization")
    pkg.__path__ = [str(PERSONALIZATION_DIR)]
    pkg.date_utils = date_utils
    sys.modules["personalization"] = pkg

    # 3. Load mock_data (MOCK_PROFILES)
    mock_data = _load_module(
        "personalization.mock_data",
        PERSONALIZATION_DIR / "mock_data.py",
    )

    # 4. Load data_store (_normalize_profile)
    data_store = _load_module(
        "personalization.data_store",
        PERSONALIZATION_DIR / "data_store.py",
    )

    MOCK_PROFILES = mock_data.MOCK_PROFILES
    _normalize_profile = data_store._normalize_profile

    print(f"Source: {PERSONALIZATION_DIR / 'mock_data.py'}")
    print(f"Found {len(MOCK_PROFILES)} mock users")

    # Normalize all profiles
    normalized: dict[str, dict] = {}
    for user_id, raw_profile in sorted(MOCK_PROFILES.items()):
        try:
            normalized[user_id] = _normalize_profile(raw_profile)
        except Exception as e:
            print(f"  WARN: {user_id} normalization failed: {e}")
            continue

    # Write output
    OUTPUT_PATH.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nWritten {len(normalized)} profiles → {OUTPUT_PATH}")

    # Summary
    for uid in sorted(normalized.keys()):
        basic = normalized[uid].get("basic", {})
        skin = basic.get("skin_type", "-")
        gender = basic.get("gender", "-")
        age = basic.get("age", "-")
        has_chat = "chat" if normalized[uid].get("chat") else "no-chat"
        print(f"  {uid}: {gender}/{age}/{skin} ({has_chat})")


if __name__ == "__main__":
    main()
