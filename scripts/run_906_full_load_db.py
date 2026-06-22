#!/usr/bin/env python3
"""Run the 906-review GraphRapping full load with source review stats snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.jobs.run_full_load import FullLoadConfig  # noqa: E402
from src.jobs.run_full_load_db import run_full_load_to_db  # noqa: E402


DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5432/graphrapping"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("GRAPHRAPPING_DATABASE_URL", DEFAULT_DSN),
    )
    parser.add_argument("--review-json", default="mockdata/review_triples_raw.json")
    parser.add_argument("--product-json", default="mockdata/product_catalog_es.json")
    parser.add_argument("--user-profiles-json", default="mockdata/user_profiles_normalized.json")
    parser.add_argument(
        "--source-review-stats-json",
        default="data/source_snapshots/product_review_stats_snowflake_latest.json",
    )
    parser.add_argument("--kg-mode", default="off", choices=("off", "on"))
    parser.add_argument("--skip-validation", action="store_true")
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    products = _load_json(Path(args.product_json))
    users = _load_json(Path(args.user_profiles_json))

    pool = await asyncpg.create_pool(args.database_url, min_size=1, max_size=2)
    try:
        result = await run_full_load_to_db(
            pool,
            FullLoadConfig(
                review_json_path=args.review_json,
                product_es_records=products,
                user_profiles=users,
                kg_mode=args.kg_mode,
                source_review_stats_json_path=args.source_review_stats_json,
            ),
            validate_after=not args.skip_validation,
            validator_options={
                "expected_min_source_review_count_6m": 516,
                "expected_min_source_avg_rating_6m": 516,
                "enforce_source_grounding": True,
            },
        )
    finally:
        await pool.close()

    print(json.dumps(_summary(result), ensure_ascii=False, indent=2, sort_keys=True))


def _summary(result: Any) -> dict[str, Any]:
    source_stats = result.in_memory.source_review_stats_by_product
    positive_6m = sum(1 for row in source_stats.values() if (row.get("source_review_count_6m") or 0) > 0)
    rating_6m = sum(1 for row in source_stats.values() if row.get("source_avg_rating_6m") is not None)
    validation = result.validation
    return {
        "run_id": result.run_id,
        "review_count": result.in_memory.review_count,
        "signal_count": result.in_memory.signal_count,
        "quarantine_count": result.in_memory.quarantine_count,
        "serving_product_count": result.in_memory.serving_product_count,
        "source_review_stats_products": len(source_stats),
        "source_review_stats_positive_6m": positive_6m,
        "source_review_stats_avg_rating_6m": rating_6m,
        "persisted": result.persisted,
        "validation_status": validation.status.value if validation else None,
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main_async())
