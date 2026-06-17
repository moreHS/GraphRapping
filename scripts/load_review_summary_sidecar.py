#!/usr/bin/env python3
"""Load ES review-summary aliases into the local GraphRapping sidecar."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.connection import close_pool, create_pool  # noqa: E402
from src.db.migrate import migrate  # noqa: E402
from src.jobs.load_review_summary_sidecar import load_review_summary_sidecar  # noqa: E402
from src.loaders.review_summary_sidecar_loader import (  # noqa: E402
    es_config_from_env,
    fetch_es_alias_docs,
    read_docs_file,
)


DEFAULT_ENV_FILE = Path("/Users/amore/workplace/prompt-eng-rag/.env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Postgres DSN. Defaults to env resolution.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Dotenv file containing ES_CLOUD_URL/KEY.")
    parser.add_argument("--long-alias", default="summary-review-long")
    parser.add_argument("--short-alias", default="summary-review-short")
    parser.add_argument("--an-date", help="Optional materialization/date label for the manifest.")
    parser.add_argument("--long-json", help="JSON/JSONL export for long summaries. If absent, fetch from ES.")
    parser.add_argument("--short-json", help="JSON/JSONL export for short summaries. If absent, fetch from ES.")
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-docs", type=int, help="Debug cap per alias. Omit for full alias export.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    long_docs, short_docs = _load_docs(args)
    pool = await create_pool(args.database_url, command_timeout=120)
    try:
        await migrate(pool)
        result = await load_review_summary_sidecar(
            pool,
            long_docs,
            short_docs,
            long_alias=args.long_alias,
            short_alias=args.short_alias,
            an_date=args.an_date,
        )
    finally:
        await close_pool()

    print(json.dumps(_printable_result(result), ensure_ascii=False, indent=2, sort_keys=True))


def _load_docs(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    es_url: str | None = None
    api_key: str | None = None

    if args.long_json:
        long_docs = read_docs_file(args.long_json)
    else:
        es_url, api_key = _ensure_es_config(args.env_file, es_url, api_key)
        long_docs = fetch_es_alias_docs(
            es_url,
            api_key,
            args.long_alias,
            page_size=args.page_size,
            max_docs=args.max_docs,
        )

    if args.short_json:
        short_docs = read_docs_file(args.short_json)
    else:
        es_url, api_key = _ensure_es_config(args.env_file, es_url, api_key)
        short_docs = fetch_es_alias_docs(
            es_url,
            api_key,
            args.short_alias,
            page_size=args.page_size,
            max_docs=args.max_docs,
        )

    return long_docs, short_docs


def _ensure_es_config(
    env_file: str,
    es_url: str | None,
    api_key: str | None,
) -> tuple[str, str]:
    if es_url and api_key:
        return es_url, api_key
    return es_config_from_env(env_file)


def _printable_result(result: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "manifest_id",
        "sidecar_rows",
        "product_count",
        "clean_lookup_product_count",
        "fetched_long_docs",
        "fetched_short_docs",
        "matched",
        "exact_category",
        "source_unique",
        "product_id_unique",
        "ambiguous_skipped",
        "not_found",
        "collision_excluded",
        "missing_source_identity_excluded",
        "deleted_stale_sidecar_rows",
    ]
    return {key: result.get(key) for key in keys if key in result}


if __name__ == "__main__":
    asyncio.run(main())
