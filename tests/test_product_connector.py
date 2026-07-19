"""IC-P product-master connector (plan §5·§6).

Contract validation (abort on 3-key violation), baseline diff (added/removed/
changed by field group, new 3-key collisions, rep-code joinability delta),
golden self-diff (change 0), staging protections/determinism, and aggregate-only
manifest (top-N SKU ids, no raw product names).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from scripts.refresh_product_catalog import (
    ProductRefreshError,
    compute_catalog_diff,
    refresh_product_catalog,
)

MOCK = Path("mockdata")


def _prod(sku: str, **over: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "SOURCE_CHANNEL": "OWN", "SOURCE_KEY_TYPE": "PRODUCT_ID",
        "SOURCE_PRODUCT_ID": f"P{sku}", "ONLINE_PROD_SERIAL_NUMBER": sku,
        "REPRESENTATIVE_PROD_CODE": "123456789",
        "ONLINE_PROD_NAME": "name", "REPRESENTATIVE_PROD_NAME": "rep name",
        "BRAND_NAME": "brand", "BRAND_CODE": "B1",
        "CTGR_L_NAME": "Beauty", "CTGR_M_NAME": "Skin",
        "CTGR_S_NAME": "", "CTGR_SS_NAME": "",
    }
    rec.update(over)
    return rec


# ---------------------------------------------------------------------------
# compute_catalog_diff (pure)
# ---------------------------------------------------------------------------

def test_diff_added_and_removed() -> None:
    diff = compute_catalog_diff([_prod("1"), _prod("3")], [_prod("1"), _prod("2")])
    assert diff.added == 1 and diff.removed == 1 and diff.changed == 0
    assert diff.sample_added_ids == ["3"] and diff.sample_removed_ids == ["2"]


def test_diff_changed_by_field_group() -> None:
    baseline = [_prod("1")]
    new = [_prod("1", ONLINE_PROD_NAME="renamed", BRAND_NAME="other",
                 CTGR_M_NAME="Hair", REPRESENTATIVE_PROD_CODE="999999999")]
    diff = compute_catalog_diff(new, baseline)
    assert diff.changed == 1
    assert diff.changed_by_field == {"name": 1, "brand": 1, "category": 1, "rep_code": 1}
    assert diff.sample_changed_ids == ["1"]


def test_diff_new_collision_group() -> None:
    baseline = [_prod("1"), _prod("2")]  # distinct 3-key tuples
    new = [_prod("1"), _prod("2"), _prod("3", SOURCE_PRODUCT_ID="P1")]  # SKU 3 collides with SKU 1
    diff = compute_catalog_diff(new, baseline)
    assert diff.new_collision_groups == 1
    assert diff.added == 1


def test_diff_joinability_delta() -> None:
    baseline = [_prod("1"), _prod("2")]  # both 9-digit joinable
    new = [_prod("1"), _prod("2", REPRESENTATIVE_PROD_CODE="Z_Z")]  # 2 becomes non-conforming
    diff = compute_catalog_diff(new, baseline)
    assert diff.joinability_delta["joinable_9digit"] == -1
    assert diff.joinability_delta["nonconforming"] == 1
    assert diff.changed_by_field["rep_code"] == 1


# ---------------------------------------------------------------------------
# Golden self-diff — change 0
# ---------------------------------------------------------------------------

def test_golden_self_diff_is_zero(tmp_path: Path) -> None:
    golden = json.loads((MOCK / "product_catalog_es.json").read_text(encoding="utf-8"))
    snap, manifest = refresh_product_catalog(golden, golden, "20260720", real_dir=tmp_path / "real")
    diff = manifest["diff"]
    assert diff["added"] == 0 and diff["removed"] == 0 and diff["changed"] == 0
    assert diff["new_collision_groups"] == 0
    assert all(v == 0 for v in diff["joinability_delta"].values())
    # Golden's 6 non-conforming rep codes are surfaced, not rejected.
    assert manifest["validation"]["violations"] == 0
    assert diff["joinability_new"]["joinable_9digit"] == 511
    assert diff["joinability_new"]["nonconforming"] == 6
    assert manifest["count"] == 517
    assert json.loads(snap.read_text(encoding="utf-8"))[0]  # snapshot written


# ---------------------------------------------------------------------------
# Contract abort
# ---------------------------------------------------------------------------

def test_refresh_aborts_on_3key_violation(tmp_path: Path) -> None:
    bad = _prod("1")
    del bad["SOURCE_CHANNEL"]  # 3-key violation
    with pytest.raises(ProductRefreshError, match="contract-violating"):
        refresh_product_catalog([bad], [_prod("1")], "20260720", real_dir=tmp_path / "real")
    # Nothing landed.
    assert not (tmp_path / "real" / "products" / "product_catalog_20260720.json").exists()


def test_nonconforming_rep_code_is_not_a_violation(tmp_path: Path) -> None:
    # A non-9-digit rep code must NOT abort the refresh (codex #4).
    snap, manifest = refresh_product_catalog(
        [_prod("1", REPRESENTATIVE_PROD_CODE="Z_Z")], [_prod("1")],
        "20260720", real_dir=tmp_path / "real",
    )
    assert manifest["validation"]["violations"] == 0
    assert snap.exists()


# ---------------------------------------------------------------------------
# Staging: manifest / determinism / protections / privacy
# ---------------------------------------------------------------------------

def test_manifest_fields_and_protections(tmp_path: Path) -> None:
    real = tmp_path / "real"
    new = [_prod("1", ONLINE_PROD_NAME="renamed"), _prod("2")]
    snap, manifest = refresh_product_catalog(new, [_prod("1"), _prod("2")], "20260720", real_dir=real)
    assert snap.name == "product_catalog_20260720.json"
    assert manifest["format"] == "product_catalog" and manifest["count"] == 2
    assert manifest["added"] == 0 and manifest["updated"] == 1 and manifest["unchanged"] == 1
    assert manifest["removed"] == 0
    assert stat.S_IMODE(os.stat(snap).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(snap.parent).st_mode) == 0o700
    assert (real / "products" / "manifest.json").exists()


def test_refresh_is_deterministic(tmp_path: Path) -> None:
    new = [_prod("2"), _prod("1")]  # unsorted → sorted-by-SKU output
    p1, _ = refresh_product_catalog(new, [], "20260720", real_dir=tmp_path / "r1")
    p2, _ = refresh_product_catalog(list(reversed(new)), [], "20260720", real_dir=tmp_path / "r2")
    assert p1.read_text(encoding="utf-8") == p2.read_text(encoding="utf-8")


def test_manifest_lists_ids_not_product_names(tmp_path: Path) -> None:
    new = [_prod("1", ONLINE_PROD_NAME="은밀한상품명")]
    _snap, manifest = refresh_product_catalog(new, [_prod("1")], "20260720", real_dir=tmp_path / "real")
    blob = json.dumps(manifest, ensure_ascii=False)
    assert "은밀한상품명" not in blob  # names never surfaced
    assert manifest["diff"]["sample_changed_ids"] == ["1"]  # only the SKU id


def test_snapshot_sorted_by_sku(tmp_path: Path) -> None:
    new = [_prod("30"), _prod("10"), _prod("20")]
    snap, _ = refresh_product_catalog(new, [], "20260720", real_dir=tmp_path / "real")
    on_disk = json.loads(snap.read_text(encoding="utf-8"))
    assert [r["ONLINE_PROD_SERIAL_NUMBER"] for r in on_disk] == ["10", "20", "30"]
