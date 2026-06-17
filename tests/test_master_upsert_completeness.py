"""
Wave 4 Task 5: `upsert_product_master` and `upsert_user_master` must refresh
all consumer-relevant non-PK columns on conflict so re-loads see fresh truth.

Contract test (skip-proof): parses the SQL string from `inspect.getsource`
and asserts each expected column appears in the `DO UPDATE SET` clause.
Behavioural PG coverage lives alongside the rest of the asyncpg-bound
integration suite.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import re

from src.db.repos import product_repo, user_repo


# product_master DDL non-PK columns (excluding created_at/updated_at meta).
_PRODUCT_REFRESHABLE_COLUMNS = {
    "product_name",
    "brand_id",
    "brand_name",
    "category_id",
    "category_name",
    "country_of_origin",
    "main_benefits",
    "price",
    "ingredients",
    "volume",
    "shade",
    "variant_family_id",
    "source_product_id",
    "source_channel",
    "source_key_type",
    "representative_product_name",
    "source_truth_source",
    "source_truth_quality",
    "source_truth_updated_at",
    "source_review_count",
    "source_review_score",
    "is_active",
}

# user_master DDL non-PK columns (excluding created_at/updated_at meta).
_USER_REFRESHABLE_COLUMNS = {
    "age",
    "age_band",
    "gender",
    "skin_type",
    "skin_tone",
    "raw_payload",
    "is_active",
}

# Always-refresh meta
_META_COLUMNS = {"updated_at"}


def _extract_do_update_set_columns(sql_text: str) -> set[str]:
    """Return the column names listed on the LHS of `DO UPDATE SET` assignments."""
    match = re.search(
        r"DO\s+UPDATE\s+SET\s+(.*?)(?:\"\"\"|RETURNING|$)",
        sql_text,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "DO UPDATE SET clause not found"
    body = match.group(1)
    return {
        m.group(1).lower()
        for m in re.finditer(r"([a-z_][a-z0-9_]*)\s*=", body, re.IGNORECASE)
    }


def _extract_insert_columns(sql_text: str, table_name: str) -> list[str]:
    """Return the ordered column list from `INSERT INTO <table> (...)`."""
    match = re.search(
        rf"insert\s+into\s+{re.escape(table_name)}\s*\((.*?)\)\s*values",
        sql_text,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"INSERT INTO {table_name} not found"
    return [c.strip() for c in match.group(1).split(",") if c.strip()]


# ---------------------------------------------------------------------------
# product_master
# ---------------------------------------------------------------------------


def test_product_insert_includes_all_refreshable_columns() -> None:
    src = inspect.getsource(product_repo.upsert_product_master)
    insert_cols = set(_extract_insert_columns(src, "product_master"))
    expected = _PRODUCT_REFRESHABLE_COLUMNS | _META_COLUMNS | {"product_id"}
    missing = expected - insert_cols
    extra = insert_cols - expected
    assert not missing, f"INSERT missing columns: {missing}"
    assert not extra, f"INSERT has unexpected columns: {extra}"


def test_product_do_update_set_refreshes_all_non_pk_columns() -> None:
    src = inspect.getsource(product_repo.upsert_product_master)
    update_cols = _extract_do_update_set_columns(src)
    expected = _PRODUCT_REFRESHABLE_COLUMNS | _META_COLUMNS
    missing = expected - update_cols
    extra = update_cols - expected
    assert not missing, f"DO UPDATE SET missing columns: {missing}"
    assert not extra, f"DO UPDATE SET has unexpected columns: {extra}"


def test_product_master_ddl_declares_source_truth_updated_at() -> None:
    ddl = (Path(__file__).parent.parent / "sql" / "ddl_raw.sql").read_text(
        encoding="utf-8",
    ).lower()
    assert "source_truth_updated_at timestamptz" in ddl
    assert (
        "alter table product_master add column if not exists "
        "source_truth_updated_at timestamptz;"
    ) in ddl


# ---------------------------------------------------------------------------
# user_master
# ---------------------------------------------------------------------------


def test_user_insert_includes_all_refreshable_columns() -> None:
    src = inspect.getsource(user_repo.upsert_user_master)
    insert_cols = set(_extract_insert_columns(src, "user_master"))
    expected = _USER_REFRESHABLE_COLUMNS | _META_COLUMNS | {"user_id"}
    missing = expected - insert_cols
    extra = insert_cols - expected
    assert not missing, f"INSERT missing columns: {missing}"
    assert not extra, f"INSERT has unexpected columns: {extra}"


def test_user_do_update_set_refreshes_all_non_pk_columns() -> None:
    src = inspect.getsource(user_repo.upsert_user_master)
    update_cols = _extract_do_update_set_columns(src)
    expected = _USER_REFRESHABLE_COLUMNS | _META_COLUMNS
    missing = expected - update_cols
    extra = update_cols - expected
    assert not missing, f"DO UPDATE SET missing columns: {missing}"
    assert not extra, f"DO UPDATE SET has unexpected columns: {extra}"
