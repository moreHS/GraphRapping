"""IC-U user-profile staging mode (plan §3·§7·§6).

Exercises the pure ``stage_user_profiles`` helper with mock profiles and an
injected date (no live DB, no ``date.now``): dated snapshot + manifest under
``real/users/``, contract self-validation recorded in the manifest, 0600/0700
protections, and determinism.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from scripts.fetch_user_profiles_pg import stage_user_profiles

_PROFILES = {
    "real_b": {"basic": {"gender": "M"}, "purchase_analysis": {}, "chat": None},
    "real_a": {
        "basic": {"gender": "F"}, "purchase_analysis": {}, "chat": None,
        "purchase_events": [{"product_id": "100", "purchased_at": "2025-01-01"}],
    },
}


def test_stage_writes_dated_snapshot_and_manifest(tmp_path: Path) -> None:
    real = tmp_path / "real"
    snapshot_path, manifest = stage_user_profiles(
        _PROFILES, "20260720", real_dir=real, backfill_stats={"rows_fetched": 2}
    )
    # Location + dated filename under the users subdirectory.
    assert snapshot_path == (real / "users" / "user_profiles_real_20260720.json").resolve()
    assert snapshot_path.exists()
    assert (real / "users" / "manifest.json").exists()

    # Snapshot content is key-sorted and complete.
    written = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert list(written.keys()) == ["real_a", "real_b"]

    # Manifest aggregates only.
    assert manifest["count"] == 2
    assert manifest["format"] == "user_profiles_normalized"
    assert manifest["generated_at"] == "20260720"
    assert manifest["added"] == 2
    assert manifest["validation"]["passed"] == 2
    assert manifest["validation"]["violations"] == 0
    assert manifest["backfill_stats"] == {"rows_fetched": 2}


def test_stage_enforces_0600_and_0700(tmp_path: Path) -> None:
    real = tmp_path / "real"
    snapshot_path, _ = stage_user_profiles(_PROFILES, "20260720", real_dir=real)
    assert stat.S_IMODE(os.stat(snapshot_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(snapshot_path.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(real).st_mode) == 0o700


def test_stage_is_deterministic_for_same_date(tmp_path: Path) -> None:
    real1 = tmp_path / "r1"
    real2 = tmp_path / "r2"
    p1, _ = stage_user_profiles(_PROFILES, "20260720", real_dir=real1)
    p2, _ = stage_user_profiles(_PROFILES, "20260720", real_dir=real2)
    assert p1.name == p2.name
    assert p1.read_text(encoding="utf-8") == p2.read_text(encoding="utf-8")


def test_stage_records_contract_violations_in_manifest(tmp_path: Path) -> None:
    real = tmp_path / "real"
    profiles = {
        "ok": {"basic": {"gender": "F"}, "purchase_analysis": {}, "chat": None},
        "bad": {"purchase_analysis": {}},  # missing required 'basic'
    }
    _snapshot, manifest = stage_user_profiles(profiles, "20260720", real_dir=real)
    assert manifest["validation"]["violations"] == 1
    assert manifest["validation"]["passed"] == 1
    reasons = [v["reason"] for v in manifest["validation"]["violations_top"]]
    assert any("missing required key: basic" in r for r in reasons)


def test_stage_rejects_dates_that_are_not_yyyymmdd(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="YYYYMMDD"):
        stage_user_profiles(_PROFILES, "2026-07-20", real_dir=tmp_path / "real")
