#!/usr/bin/env python3
"""Fetch source-grounded product review stats from Snowflake.

The output is the dedicated GraphRapping `product_review_stats` snapshot. It is
not a product master snapshot and must include 6-month review stats.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.loaders.source_review_stats_loader import (  # noqa: E402
    build_source_review_stats_sql,
    product_review_stats_rows,
)


SUPPORTED_CHANNELS = ("031", "036", "039", "048")
DEFAULT_AP_DATA_UTILS_SRC = Path("/Users/amore/workplace/ap-data-utils/src")


def parse_args() -> argparse.Namespace:
    snapshot_date = date.today().isoformat()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-identity-snapshot",
        default="data/source_snapshots/product_master_source_identity_latest.json",
        help="JSON snapshot containing SOURCE_CHANNEL/SOURCE_PRODUCT_ID records.",
    )
    parser.add_argument(
        "--output",
        default=f"data/source_snapshots/product_review_stats_snowflake_{snapshot_date}.json",
        help="Output JSON path.",
    )
    parser.add_argument(
        "--latest-output",
        default="data/source_snapshots/product_review_stats_snowflake_latest.json",
        help="Latest-copy JSON path.",
    )
    parser.add_argument("--snapshot-date", default=snapshot_date)
    parser.add_argument("--channels", default=",".join(SUPPORTED_CHANNELS))
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument(
        "--ap-data-utils-src",
        default=str(DEFAULT_AP_DATA_UTILS_SRC),
        help="Path containing nlp_data_utils for Snowflake key-pair connection.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print query plan without opening a Snowflake connection.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    channels = tuple(channel.strip() for channel in args.channels.split(",") if channel.strip())
    identities = _load_source_identities(Path(args.source_identity_snapshot), channels)

    if args.dry_run:
        print(json.dumps(_dry_run_plan(identities), ensure_ascii=False, indent=2, sort_keys=True))
        return

    conn = _create_snowflake_connection(Path(args.ap_data_utils_src))
    try:
        records = _fetch_stats_records(
            conn,
            identities=identities,
            chunk_size=args.chunk_size,
            source_label=f"snowflake:f_prd_rv_hist:{args.snapshot_date}",
        )
    finally:
        conn.close()

    payload = _build_payload(
        records,
        source_identity_snapshot=args.source_identity_snapshot,
        snapshot_date=args.snapshot_date,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )

    latest = Path(args.latest_output)
    latest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output, latest)

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    print(f"wrote {output}")
    print(f"updated {latest}")


def _load_source_identities(
    path: Path,
    channels: Iterable[str],
) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records", data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise TypeError("source identity snapshot must be a list or object with records")

    allowed = set(channels)
    identities: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        channel = _clean(record.get("SOURCE_CHANNEL") or record.get("source_channel"))
        product_id = _clean(
            record.get("SOURCE_PRODUCT_ID")
            or record.get("source_product_id")
            or record.get("product_id")
        )
        key_type = _clean(record.get("SOURCE_KEY_TYPE") or record.get("source_key_type"))
        if not channel or not product_id or channel not in allowed:
            continue
        identities.append({
            "product_id": product_id,
            "source_channel": channel,
            "source_key_type": key_type or _default_key_type(channel),
        })
    return identities


def _fetch_stats_records(
    conn: Any,
    *,
    identities: list[dict[str, str]],
    chunk_size: int,
    source_label: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    by_channel: dict[str, list[str]] = {}
    for identity in identities:
        by_channel.setdefault(identity["source_channel"], []).append(identity["product_id"])

    for channel, product_ids in sorted(by_channel.items()):
        unique_ids = sorted(set(product_ids))
        for chunk in _chunked(unique_ids, chunk_size):
            sql = build_source_review_stats_sql(chunk, source_channel=channel)
            rows = _exec_dicts(conn, sql)
            for row in rows:
                row["source"] = source_label
            records.extend(product_review_stats_rows(rows))
    records.sort(key=lambda row: (
        str(row.get("product_id")),
        str(row.get("source_channel")),
        str(row.get("source_key_type")),
    ))
    return records


def _create_snowflake_connection(ap_data_utils_src: Path) -> Any:
    if ap_data_utils_src.exists() and str(ap_data_utils_src) not in sys.path:
        sys.path.insert(0, str(ap_data_utils_src))
    try:
        from nlp_data_utils.snowflake.connection import create_snowflake_connection
    except ImportError as exc:
        raise RuntimeError(
            "Cannot import nlp_data_utils.snowflake.connection. "
            "Pass --ap-data-utils-src or install ap-data-utils."
        ) from exc

    env = _required_env(
        "SNF_ACCOUNT",
        "SNF_USER",
        "SNF_ROLE",
        "SNF_TARGET_WAREHOUSE",
        "SNF_TARGET_DB",
        "SNF_TARGET_SCHEMA",
        "AWS_SECRET_NAME",
        "AWS_REGION",
    )
    return create_snowflake_connection(
        account=env["SNF_ACCOUNT"],
        user=env["SNF_USER"],
        role=env["SNF_ROLE"],
        warehouse=env["SNF_TARGET_WAREHOUSE"],
        database=env["SNF_TARGET_DB"],
        schema=env["SNF_TARGET_SCHEMA"],
        secret_name=env["AWS_SECRET_NAME"],
        region_name=env["AWS_REGION"],
    )


def _exec_dicts(conn: Any, sql: str) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def _build_payload(
    records: list[dict[str, Any]],
    *,
    source_identity_snapshot: str,
    snapshot_date: str,
) -> dict[str, Any]:
    product_counts = Counter(str(row["product_id"]) for row in records)
    duplicate_product_ids = sorted(pid for pid, count in product_counts.items() if count > 1)
    channel_counts = Counter(str(row.get("source_channel")) for row in records)
    positive_6m = sum(1 for row in records if (row.get("source_review_count_6m") or 0) > 0)
    rating_6m = sum(1 for row in records if row.get("source_avg_rating_6m") is not None)
    return {
        "records": records,
        "summary": {
            "snapshot_date": snapshot_date,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_identity_snapshot": source_identity_snapshot,
            "record_count": len(records),
            "distinct_product_id_count": len(product_counts),
            "duplicate_product_ids": duplicate_product_ids,
            "source_channel_counts": dict(sorted(channel_counts.items())),
            "positive_review_count_6m_records": positive_6m,
            "avg_rating_6m_records": rating_6m,
        },
    }


def _dry_run_plan(identities: list[dict[str, str]]) -> dict[str, Any]:
    channel_counts = Counter(identity["source_channel"] for identity in identities)
    product_counts = Counter(identity["product_id"] for identity in identities)
    return {
        "identity_count": len(identities),
        "source_channel_counts": dict(sorted(channel_counts.items())),
        "duplicate_product_ids": sorted(pid for pid, count in product_counts.items() if count > 1),
    }


def _required_env(*names: str) -> dict[str, str]:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required Snowflake environment variables: {', '.join(missing)}")
    return {name: os.environ[name] for name in names}


def _chunked(values: list[str], size: int) -> Iterator[list[str]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _default_key_type(channel: str) -> str:
    return "ecp_onln_prd_srno" if channel == "031" else "chn_prd_cd"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


if __name__ == "__main__":
    main()
