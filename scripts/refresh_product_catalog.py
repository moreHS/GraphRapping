#!/usr/bin/env python3
"""Product-master connector (readiness) — contract validation + baseline diff.

IC-P (fable_doc/plans/2026-07-19_input_connectors_readiness.md §5). Prepares the
"real DB" transition for the product-master source WITHOUT touching the pipeline:
takes a NEW catalog file, validates it against the product-catalog contract,
diffs it against the existing catalog, and lands the new catalog as a dated
snapshot under the git-ignored ``mockdata/real/products/`` tree with an
aggregate-only manifest. The demo / full-load entry points consume the snapshot
by pointing ``GRAPHRAPPING_PRODUCT_CATALOG_JSON`` at it (IC-1 wired the env).

CONTRACT (plan §1 / codex #4)
-----------------------------
Rejection is limited to the 3-key source identity (``SOURCE_CHANNEL`` /
``SOURCE_KEY_TYPE`` / ``SOURCE_PRODUCT_ID``), the serving id
(``ONLINE_PROD_SERIAL_NUMBER``), and the collision-marker type. A non-9-digit
``REPRESENTATIVE_PROD_CODE`` is NEVER a rejection reason — the golden catalog
itself carries 6 non-conforming rep codes — so it is surfaced only as
purchase-join observability (:func:`report_rep_code_joinability`). Any contract
violation aborts the refresh (a structurally broken catalog is never landed).

DIFF REPORT
-----------
Products are keyed by ``ONLINE_PROD_SERIAL_NUMBER`` (the serving SKU id). The
diff reports added / removed / changed counts, which tracked field group changed
(name / brand / category / rep_code), newly-appearing 3-key collision groups, and
the purchase-join (9-digit rep_code) delta. Everything is aggregate; only the
top-N changed/added/removed SKU ids are listed (never product names or other raw
columns), so the manifest is safe to keep even though the snapshot is git-ignored.

RE-EXTRACTION (IC-3, not here)
------------------------------
Automated re-extraction from the ES/Snowflake lineage is a later track. This
connector consumes a file the operator produced via the documented regeneration
procedure — see docs/architecture/v260605_906_fixture_lineage.md §2 (생성 경로)
and docs/architecture/product_master_real_snapshot_2026_06_16.md.

Usage:
    python scripts/refresh_product_catalog.py --input new_catalog.json
    python scripts/refresh_product_catalog.py --input new.json --baseline old.json --date 20260720
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Paths ──
GRAPHRAPPING_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_DIR = GRAPHRAPPING_ROOT / "mockdata" / "real"
DEFAULT_BASELINE = GRAPHRAPPING_ROOT / "mockdata" / "product_catalog_es.json"

# Direct-run safety (see fetch_review_triples for rationale).
if str(GRAPHRAPPING_ROOT) not in sys.path:
    sys.path.insert(0, str(GRAPHRAPPING_ROOT))

# Serving SKU id — the product diff key.
PRODUCT_KEY = "ONLINE_PROD_SERIAL_NUMBER"
# 3-key source identity — a collision is >1 distinct SKU under one tuple.
IDENTITY_3KEY = ("SOURCE_CHANNEL", "SOURCE_KEY_TYPE", "SOURCE_PRODUCT_ID")
# Tracked field groups for change classification (which attributes moved).
TRACKED_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "name": ("ONLINE_PROD_NAME", "REPRESENTATIVE_PROD_NAME"),
    "brand": ("BRAND_NAME", "BRAND_CODE"),
    "category": ("CTGR_L_NAME", "CTGR_M_NAME", "CTGR_S_NAME", "CTGR_SS_NAME"),
    "rep_code": ("REPRESENTATIVE_PROD_CODE",),
}


class ProductRefreshError(RuntimeError):
    """The refresh was refused (contract violation in the new catalog)."""


# =============================================================================
# Diff (pure)
# =============================================================================

@dataclass
class CatalogDiff:
    """Aggregate baseline→new catalog delta. Holds only counts + top-N SKU ids."""

    baseline_count: int
    new_count: int
    added: int
    removed: int
    changed: int
    changed_by_field: dict[str, int] = field(default_factory=dict)
    new_collision_groups: int = 0
    joinability_baseline: dict[str, Any] = field(default_factory=dict)
    joinability_new: dict[str, Any] = field(default_factory=dict)
    joinability_delta: dict[str, int] = field(default_factory=dict)
    sample_added_ids: list[str] = field(default_factory=list)
    sample_removed_ids: list[str] = field(default_factory=list)
    sample_changed_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_count": self.baseline_count,
            "new_count": self.new_count,
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "changed_by_field": dict(self.changed_by_field),
            "new_collision_groups": self.new_collision_groups,
            "joinability_baseline": dict(self.joinability_baseline),
            "joinability_new": dict(self.joinability_new),
            "joinability_delta": dict(self.joinability_delta),
            "sample_added_ids": list(self.sample_added_ids),
            "sample_removed_ids": list(self.sample_removed_ids),
            "sample_changed_ids": list(self.sample_changed_ids),
        }


def _index_by_sku(records: Sequence[Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        sku = rec.get(PRODUCT_KEY)
        sku_s = str(sku).strip() if sku is not None else ""
        if sku_s:
            index[sku_s] = dict(rec)
    return index


def _collision_tuples(records: Sequence[Any]) -> set[tuple[str, ...]]:
    """3-key tuples that map to >1 distinct SKU (i.e., an identity collision)."""
    by_tuple: dict[tuple[str, ...], set[str]] = {}
    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        key = tuple(str(rec.get(k, "")).strip() for k in IDENTITY_3KEY)
        sku = str(rec.get(PRODUCT_KEY, "")).strip()
        if sku:
            by_tuple.setdefault(key, set()).add(sku)
    return {key for key, skus in by_tuple.items() if len(skus) > 1}


def _changed_groups(new_rec: Mapping[str, Any], base_rec: Mapping[str, Any]) -> set[str]:
    changed: set[str] = set()
    for group, fields in TRACKED_FIELD_GROUPS.items():
        for f in fields:
            if str(new_rec.get(f, "")).strip() != str(base_rec.get(f, "")).strip():
                changed.add(group)
                break
    return changed


def compute_catalog_diff(
    new_records: Sequence[Any],
    baseline_records: Sequence[Any],
    *,
    sample_n: int = 10,
) -> CatalogDiff:
    """Diff a new catalog against a baseline (pure — no I/O)."""
    from src.ingest.input_contracts import report_rep_code_joinability

    new_idx = _index_by_sku(new_records)
    base_idx = _index_by_sku(baseline_records)
    new_keys, base_keys = set(new_idx), set(base_idx)

    added_ids = sorted(new_keys - base_keys)
    removed_ids = sorted(base_keys - new_keys)
    changed_ids: list[str] = []
    changed_by_field = {group: 0 for group in TRACKED_FIELD_GROUPS}
    for sku in sorted(new_keys & base_keys):
        groups = _changed_groups(new_idx[sku], base_idx[sku])
        if groups:
            changed_ids.append(sku)
            for group in groups:
                changed_by_field[group] += 1

    new_collisions = len(_collision_tuples(new_records) - _collision_tuples(baseline_records))

    jb_new = report_rep_code_joinability(new_records).to_dict()
    jb_base = report_rep_code_joinability(baseline_records).to_dict()
    jb_delta = {
        k: int(jb_new[k]) - int(jb_base[k])
        for k in ("total", "joinable_9digit", "nonconforming", "missing")
    }

    return CatalogDiff(
        baseline_count=len(base_idx),
        new_count=len(new_idx),
        added=len(added_ids),
        removed=len(removed_ids),
        changed=len(changed_ids),
        changed_by_field=changed_by_field,
        new_collision_groups=new_collisions,
        joinability_baseline=jb_base,
        joinability_new=jb_new,
        joinability_delta=jb_delta,
        sample_added_ids=added_ids[:sample_n],
        sample_removed_ids=removed_ids[:sample_n],
        sample_changed_ids=changed_ids[:sample_n],
    )


# =============================================================================
# Refresh + staging (pure w.r.t. the clock — date is injected)
# =============================================================================

def refresh_product_catalog(
    new_records: Sequence[Any],
    baseline_records: Sequence[Any],
    date_str: str,
    *,
    real_dir: Path = REAL_DATA_DIR,
    sample_n: int = 10,
) -> tuple[Path, dict[str, Any]]:
    """Validate + diff + land a new product catalog snapshot (§5).

    Aborts (writes nothing) if the new catalog has ANY contract violation
    (3-key / serving-id / collision-marker). Otherwise lands the full new catalog
    as a dated snapshot + an aggregate manifest carrying the baseline diff and the
    rep-code joinability report. Returns ``(snapshot_path, manifest_dict)``;
    ``date_str`` is caller-injected (deterministic).
    """
    from src.ingest import input_contracts, staging

    report = input_contracts.validate_records(new_records, "product_catalog")
    if report.violations:
        raise ProductRefreshError(
            f"new catalog has {report.violations}/{report.total} contract-violating "
            f"record(s) — refresh aborted (top reasons: {report.violations_top[:3]})"
        )

    diff = compute_catalog_diff(new_records, baseline_records, sample_n=sample_n)

    products_dir = staging.ensure_staging_dir("products", real_dir=real_dir)
    filename = staging.snapshot_filename("product_catalog", date_str)
    snapshot_path = staging.validate_staging_path(products_dir / filename, real_dir=real_dir)
    ordered = sorted(
        (dict(r) for r in new_records if isinstance(r, Mapping)),
        key=lambda r: str(r.get(PRODUCT_KEY, "")),
    )
    staging.write_json_atomic(
        snapshot_path, json.dumps(ordered, ensure_ascii=False, indent=2) + "\n"
    )

    unchanged = (diff.new_count - diff.added) - diff.changed
    manifest = staging.StagingManifest(
        path=str(snapshot_path),
        format="product_catalog",
        count=len(ordered),
        generated_at=date_str,
        added=diff.added,
        updated=diff.changed,
        unchanged=unchanged,
        conflict=0,
        validation=report.to_manifest_dict(),
        extra={"diff": diff.to_dict(), "removed": diff.removed},
    )
    staging.write_manifest(products_dir / "manifest.json", manifest)
    return snapshot_path, manifest.to_dict()


# =============================================================================
# CLI
# =============================================================================

def _load_records(path: Path) -> list[Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ProductRefreshError(f"{path}: expected a JSON array of catalog records")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="new product-catalog JSON file (array of records)")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help=f"catalog to diff against (default: {DEFAULT_BASELINE})")
    parser.add_argument("--date", default=None, help="snapshot date YYYYMMDD (default: today)")
    parser.add_argument("--sample-n", type=int, default=10,
                        help="max SKU ids listed per change bucket (default 10)")
    args = parser.parse_args()

    date_str = args.date or _dt.datetime.now().strftime("%Y%m%d")
    new_records = _load_records(args.input)
    baseline_records = _load_records(args.baseline)
    snapshot_path, manifest = refresh_product_catalog(
        new_records, baseline_records, date_str, sample_n=args.sample_n
    )

    print("=== PRODUCT CATALOG REFRESH (aggregates only) ===")
    print(json.dumps({
        "count": manifest["count"],
        "validation": manifest["validation"],
        "diff": manifest["diff"],
    }, ensure_ascii=False, indent=2))
    print(f"\nLanded {manifest['count']} products → {snapshot_path} (mode 0600)")


if __name__ == "__main__":
    main()
