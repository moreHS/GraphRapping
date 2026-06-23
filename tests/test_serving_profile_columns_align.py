"""
P3-1: serving_*_profile columns must be aligned across the 3 layers:
  - sql/ddl_mart.sql (CREATE TABLE + ALTER TABLE)
  - src/db/repos/mart_repo.py (UPSERT INSERT column list)
  - src/mart/build_serving_views.py (builder output dict keys)

Single source of truth: src/mart/serving_profile_schema.py.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

from src.db.repos import mart_repo
from src.mart.build_serving_views import (
    build_serving_product_profile,
    build_serving_user_profile,
)
from src.mart.serving_profile_schema import (
    SERVING_PRODUCT_PROFILE_COLUMNS,
    SERVING_USER_PROFILE_COLUMNS,
)


_DDL_PATH = Path(__file__).parent.parent / "sql" / "ddl_mart.sql"


class _CaptureUow:
    as_of_ts = "2026-06-16T00:00:00Z"

    def __init__(self) -> None:
        self.query: str | None = None
        self.args: tuple[object, ...] = ()

    async def execute(self, query: str, *args: object) -> None:
        self.query = query
        self.args = args


def _extract_table_columns(ddl_text: str, table_name: str) -> set[str]:
    """Collect column names declared for a table across CREATE TABLE and ALTER TABLE.

    Robust enough for the project's DDL conventions: lower-case identifiers,
    one-column-per-line CREATE bodies, `ADD COLUMN IF NOT EXISTS <name>` ALTERs.
    """
    columns: set[str] = set()

    create_pattern = re.compile(
        rf"create\s+table\s+(?:if\s+not\s+exists\s+)?{re.escape(table_name)}\s*\((.*?)\);",
        re.IGNORECASE | re.DOTALL,
    )
    create_match = create_pattern.search(ddl_text)
    if create_match:
        body = create_match.group(1)
        for raw_line in body.splitlines():
            line = raw_line.split("--", 1)[0].strip().rstrip(",")
            if not line:
                continue
            # Skip table-level constraints
            head = line.split()[0].lower()
            if head in {"primary", "constraint", "check", "unique", "foreign"}:
                continue
            ident = re.match(r"([a-z_][a-z0-9_]*)", line, re.IGNORECASE)
            if ident:
                columns.add(ident.group(1).lower())

    alter_pattern = re.compile(
        rf"alter\s+table\s+{re.escape(table_name)}\s+add\s+column\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*)",
        re.IGNORECASE,
    )
    for match in alter_pattern.finditer(ddl_text):
        columns.add(match.group(1).lower())

    return columns


def _sample_product_master() -> dict:
    return {
        "product_id": "p1",
        "brand_id": "b1",
        "brand_name": "Brand1",
        "category_id": "c1",
        "category_name": "Cat1",
        "country_of_origin": "KR",
        "price": 12000.0,
        "price_band": "mid",
        "variant_family_id": "vf1",
        "main_benefits": ["benefit_a"],
        "ingredients": ["ing_a"],
        "_es_meta": {"REPRESENTATIVE_PROD_NAME": "Rep Name"},
    }


def _sample_user_master() -> dict:
    return {
        "user_id": "u1",
        "age_band": "20s",
        "gender": "F",
        "skin_type": "oily",
        "skin_tone": "warm",
    }


def test_builder_output_keys_match_product_columns() -> None:
    out = build_serving_product_profile(
        product_master=_sample_product_master(),
        agg_signals=[],
        window_type="all",
        concept_links=[],
    )
    assert set(out.keys()) == set(SERVING_PRODUCT_PROFILE_COLUMNS), (
        f"Builder output drift\n"
        f"  missing in builder: {set(SERVING_PRODUCT_PROFILE_COLUMNS) - set(out.keys())}\n"
        f"  extra in builder:   {set(out.keys()) - set(SERVING_PRODUCT_PROFILE_COLUMNS)}"
    )


def test_builder_output_keys_match_user_columns() -> None:
    out = build_serving_user_profile(
        user_master=_sample_user_master(),
        preferences=[],
    )
    assert set(out.keys()) == set(SERVING_USER_PROFILE_COLUMNS), (
        f"User builder output drift\n"
        f"  missing in builder: {set(SERVING_USER_PROFILE_COLUMNS) - set(out.keys())}\n"
        f"  extra in builder:   {set(out.keys()) - set(SERVING_USER_PROFILE_COLUMNS)}"
    )


def test_user_builder_emits_scoped_preferences_from_source_mix() -> None:
    out = build_serving_user_profile(
        user_master=_sample_user_master(),
        preferences=[
            {
                "preference_edge_type": "PREFERS_KEYWORD",
                "dst_node_id": "concept:Keyword:매트",
                "weight": 0.8,
                "source_mix": {
                    "chat": 1.0,
                    "scope_group": "makeup",
                    "source_sections": ["chat.makeup.preferred_texture"],
                },
            }
        ],
    )

    assert out["preferred_keyword_ids"] == [{"id": "concept:Keyword:매트", "weight": 0.8}]
    assert out["scoped_preference_ids"] == [
        {
            "edge_type": "PREFERS_KEYWORD",
            "id": "concept:Keyword:매트",
            "weight": 0.8,
            "scope_group": "makeup",
            "source_sections": ["chat.makeup.preferred_texture"],
        }
    ]


def test_user_builder_keeps_active_category_separate_from_preferred_category() -> None:
    out = build_serving_user_profile(
        user_master=_sample_user_master(),
        preferences=[
            {
                "preference_edge_type": "ACTIVE_IN_CATEGORY",
                "dst_node_id": "concept:Category:skincare",
                "weight": 0.7,
            }
        ],
    )

    assert out["active_category_ids"] == [{"id": "concept:Category:skincare", "weight": 0.7}]
    assert out["preferred_category_ids"] == []


_META_COLUMNS = {"is_active", "updated_at"}


def _extract_insert_columns(sql_text: str, table_name: str) -> list[str]:
    """Parse `INSERT INTO <table> (col1, col2, ...)` column list, in order."""
    match = re.search(
        rf"insert\s+into\s+{re.escape(table_name)}\s*\((.*?)\)\s*values",
        sql_text,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"INSERT INTO {table_name} not found in upsert source"
    return [c.strip() for c in match.group(1).split(",") if c.strip()]


def _extract_update_set_columns(sql_text: str, table_name: str) -> set[str]:
    """Parse `ON CONFLICT ... DO UPDATE SET col=...` target column names."""
    match = re.search(
        r"on\s+conflict[^)]*\)\s*do\s+update\s+set\s+(.*?)\s*(?:\"\"\"|$)",
        sql_text,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"DO UPDATE SET clause not found for {table_name}"
    body = match.group(1)
    return {
        m.group(1).lower()
        for m in re.finditer(r"([a-z_][a-z0-9_]*)\s*=", body, re.IGNORECASE)
    }


def test_upsert_sql_column_list_matches_product_columns() -> None:
    """INSERT column list must equal constant + updated_at meta (PK already in constant)."""
    src = inspect.getsource(mart_repo.upsert_serving_product_profile)
    insert_cols = _extract_insert_columns(src, "serving_product_profile")
    expected = list(SERVING_PRODUCT_PROFILE_COLUMNS) + ["updated_at"]
    assert insert_cols == expected, (
        f"upsert_serving_product_profile INSERT column drift\n"
        f"  got:      {insert_cols}\n"
        f"  expected: {expected}"
    )


def test_upsert_sql_column_list_matches_user_columns() -> None:
    src = inspect.getsource(mart_repo.upsert_serving_user_profile)
    insert_cols = _extract_insert_columns(src, "serving_user_profile")
    expected = list(SERVING_USER_PROFILE_COLUMNS) + ["updated_at"]
    assert insert_cols == expected, (
        f"upsert_serving_user_profile INSERT column drift\n"
        f"  got:      {insert_cols}\n"
        f"  expected: {expected}"
    )


def test_upsert_do_update_set_targets_product_columns() -> None:
    """DO UPDATE SET must touch every non-PK column (PK = product_id)."""
    src = inspect.getsource(mart_repo.upsert_serving_product_profile)
    set_cols = _extract_update_set_columns(src, "serving_product_profile")
    expected = (set(SERVING_PRODUCT_PROFILE_COLUMNS) - {"product_id"}) | {"updated_at"}
    assert set_cols == expected, (
        f"upsert_serving_product_profile DO UPDATE SET drift\n"
        f"  missing in SET: {sorted(expected - set_cols)}\n"
        f"  extra in SET:   {sorted(set_cols - expected)}"
    )


def test_upsert_do_update_set_targets_user_columns() -> None:
    """DO UPDATE SET must touch every non-PK column (PK = user_id)."""
    src = inspect.getsource(mart_repo.upsert_serving_user_profile)
    set_cols = _extract_update_set_columns(src, "serving_user_profile")
    expected = (set(SERVING_USER_PROFILE_COLUMNS) - {"user_id"}) | {"updated_at"}
    assert set_cols == expected, (
        f"upsert_serving_user_profile DO UPDATE SET drift\n"
        f"  missing in SET: {sorted(expected - set_cols)}\n"
        f"  extra in SET:   {sorted(set_cols - expected)}"
    )


def _captured_serving_product_payload(row: dict) -> dict[str, object]:
    uow = _CaptureUow()
    asyncio.run(mart_repo.upsert_serving_product_profile(uow, row))  # type: ignore[arg-type]
    src = inspect.getsource(mart_repo.upsert_serving_product_profile)
    insert_cols = _extract_insert_columns(src, "serving_product_profile")
    assert len(insert_cols) == len(uow.args)
    return dict(zip(insert_cols, uow.args, strict=True))


def test_upsert_serving_product_profile_preserves_missing_source_stats_as_none() -> None:
    payload = _captured_serving_product_payload({"product_id": "p-null"})

    assert payload["source_review_count_6m"] is None
    assert payload["source_review_score_count_6m"] is None
    assert payload["source_review_count_all"] is None
    assert payload["source_review_score_count_all"] is None


def test_upsert_serving_product_profile_preserves_explicit_zero_source_stats() -> None:
    payload = _captured_serving_product_payload({
        "product_id": "p-zero",
        "source_review_count_6m": 0,
        "source_review_score_count_6m": 0,
        "source_review_count_all": 0,
        "source_review_score_count_all": 0,
    })

    assert payload["source_review_count_6m"] == 0
    assert payload["source_review_score_count_6m"] == 0
    assert payload["source_review_count_all"] == 0
    assert payload["source_review_score_count_all"] == 0


def test_ddl_columns_equal_product_constants_plus_meta() -> None:
    """DDL must match the constant exactly, modulo well-known meta columns."""
    ddl_text = _DDL_PATH.read_text(encoding="utf-8")
    ddl_columns = _extract_table_columns(ddl_text, "serving_product_profile")
    expected = set(SERVING_PRODUCT_PROFILE_COLUMNS) | _META_COLUMNS
    assert ddl_columns == expected, (
        f"DDL serving_product_profile column drift\n"
        f"  missing in DDL: {sorted(expected - ddl_columns)}\n"
        f"  extra in DDL:   {sorted(ddl_columns - expected)}"
    )


def test_ddl_source_review_count_fields_are_nullable_without_defaults() -> None:
    ddl_text = _DDL_PATH.read_text(encoding="utf-8")
    create_match = re.search(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?serving_product_profile\s*\((.*?)\);",
        ddl_text,
        re.IGNORECASE | re.DOTALL,
    )
    assert create_match
    create_body = create_match.group(1)

    for field in (
        "source_review_count_6m",
        "source_review_score_count_6m",
        "source_review_count_all",
        "source_review_score_count_all",
    ):
        assert re.search(rf"\b{field}\s+int\s*,", create_body, re.IGNORECASE)
        assert f"ADD COLUMN IF NOT EXISTS {field} int;" in ddl_text
        assert f"ALTER COLUMN {field} DROP NOT NULL;" in ddl_text
        assert f"ALTER COLUMN {field} DROP DEFAULT;" in ddl_text


def test_ddl_columns_equal_user_constants_plus_meta() -> None:
    ddl_text = _DDL_PATH.read_text(encoding="utf-8")
    ddl_columns = _extract_table_columns(ddl_text, "serving_user_profile")
    expected = set(SERVING_USER_PROFILE_COLUMNS) | _META_COLUMNS
    assert ddl_columns == expected, (
        f"DDL serving_user_profile column drift\n"
        f"  missing in DDL: {sorted(expected - ddl_columns)}\n"
        f"  extra in DDL:   {sorted(ddl_columns - expected)}"
    )
