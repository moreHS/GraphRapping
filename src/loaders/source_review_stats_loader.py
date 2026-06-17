"""
Snowflake source review stats loader helpers.

The functions in this module are intentionally pure: they build SQL strings
for upstream Snowflake execution and parse Snowflake-like dict rows into the
GraphRapping product review stats contract. Execution/wiring lives outside this
module so tests can lock the source-truth SQL shape without a warehouse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence


SNOWFLAKE_SOURCE = "snowflake:f_prd_rv_hist"

_SIX_MONTH_REVIEW_WINDOW = (
    "fprh.stnd_ymd BETWEEN DATEADD(month, -6, CURRENT_DATE()) AND CURRENT_DATE()"
)

_CHANNEL_KEY_TYPES = {
    "031": "ecp_onln_prd_srno",
    "036": "chn_prd_cd",
    "039": "chn_prd_cd",
    "048": "chn_prd_cd",
}


@dataclass(frozen=True)
class SourceReviewStats:
    product_id: str
    source_channel: str | None
    source_key_type: str | None
    product_name: str | None
    representative_product_name: str | None
    brand_id: str | None
    brand_name: str | None
    review_count_6m: int
    score_count_6m: int
    avg_rating_6m: float | None
    review_min_date_6m: date | None
    review_max_date_6m: date | None
    review_count_all: int
    score_count_all: int
    avg_rating_all: float | None
    review_min_date_all: date | None
    review_max_date_all: date | None
    source: str = SNOWFLAKE_SOURCE

    def to_product_review_stats_row(self) -> dict[str, Any]:
        """Return the persistence row expected by product_repo helpers."""
        return {
            "product_id": self.product_id,
            "source_channel": self.source_channel,
            "source_key_type": self.source_key_type,
            "source_review_count_6m": self.review_count_6m,
            "source_review_score_count_6m": self.score_count_6m,
            "source_avg_rating_6m": self.avg_rating_6m,
            "source_review_min_date_6m": self.review_min_date_6m,
            "source_review_max_date_6m": self.review_max_date_6m,
            "source_review_count_all": self.review_count_all,
            "source_review_score_count_all": self.score_count_all,
            "source_avg_rating_all": self.avg_rating_all,
            "source_review_min_date_all": self.review_min_date_all,
            "source_review_max_date_all": self.review_max_date_all,
            "source": self.source,
        }


def sql_literal(value: str) -> str:
    """Escape a value for Snowflake SQL literal lists in pure SQL builders."""
    return "'" + str(value).replace("'", "''") + "'"


def build_source_review_stats_sql(
    product_ids: Sequence[str],
    *,
    source_channel: str,
) -> str:
    """Build source review stats SQL for one supported own channel.

    Channel semantics:
    - 031: key by TO_VARCHAR(dcpm.ecp_onln_prd_srno)
    - 036/039/048: key by TO_VARCHAR(fprh.chn_prd_cd)
    """
    channel = str(source_channel)
    source_key_type = _CHANNEL_KEY_TYPES.get(channel)
    if source_key_type is None:
        raise ValueError(f"Unsupported source_channel for source review stats: {source_channel!r}")

    product_list = _sql_literal_list(product_ids)
    if channel == "031":
        return _build_031_source_review_stats_sql(
            channel=channel,
            product_ids_sql=product_list,
            source_key_type=source_key_type,
        )
    return _build_channel_product_review_stats_sql(
        channel=channel,
        product_ids_sql=product_list,
        source_key_type=source_key_type,
    )


def build_031_source_review_stats_sql(product_ids: Sequence[str]) -> str:
    """Convenience builder for own channel 031."""
    return build_source_review_stats_sql(product_ids, source_channel="031")


def build_non_031_source_review_stats_sql(
    product_ids: Sequence[str],
    *,
    source_channel: str,
) -> str:
    """Convenience builder for own non-031 channels keyed by chn_prd_cd."""
    if str(source_channel) == "031":
        raise ValueError("Use build_031_source_review_stats_sql for channel '031'.")
    return build_source_review_stats_sql(product_ids, source_channel=str(source_channel))


def parse_source_review_stats_row(row: Mapping[str, Any]) -> SourceReviewStats:
    """Parse a Snowflake-like row mapping into SourceReviewStats.

    Snowflake clients commonly expose uppercase column names; tests and local
    callers often use lowercase. This parser accepts both.
    """
    source_channel = _get(row, "source_channel", "chn_cd")
    source_channel_str = str(source_channel) if source_channel is not None else None
    source_key_type = _get(row, "source_key_type")
    if source_key_type is None and source_channel_str in _CHANNEL_KEY_TYPES:
        source_key_type = _CHANNEL_KEY_TYPES[source_channel_str]

    score_count_6m = _to_int(_get(row, "score_count_6m"))
    score_count_all = _to_int(_get(row, "score_count_all"))

    return SourceReviewStats(
        product_id=str(_get_required(row, "product_id")),
        source_channel=source_channel_str,
        source_key_type=str(source_key_type) if source_key_type is not None else None,
        product_name=_to_optional_str(_get(row, "product_name")),
        representative_product_name=_to_optional_str(_get(row, "representative_product_name")),
        brand_id=_to_optional_str(_get(row, "brand_id")),
        brand_name=_to_optional_str(_get(row, "brand_name")),
        review_count_6m=_to_int(_get(row, "review_count_6m")),
        score_count_6m=score_count_6m,
        avg_rating_6m=_to_float(_get(row, "avg_rating_6m")) if score_count_6m > 0 else None,
        review_min_date_6m=_to_date(_get(row, "review_min_date_6m")),
        review_max_date_6m=_to_date(_get(row, "review_max_date_6m")),
        review_count_all=_to_int(_get(row, "review_count_all")),
        score_count_all=score_count_all,
        avg_rating_all=_to_float(_get(row, "avg_rating_all")) if score_count_all > 0 else None,
        review_min_date_all=_to_date(_get(row, "review_min_date_all")),
        review_max_date_all=_to_date(_get(row, "review_max_date_all")),
        source=_to_optional_str(_get(row, "source")) or SNOWFLAKE_SOURCE,
    )


def parse_source_review_stats_rows(rows: Iterable[Mapping[str, Any]]) -> list[SourceReviewStats]:
    return [parse_source_review_stats_row(row) for row in rows]


def product_review_stats_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Parse rows and return product_review_stats persistence dictionaries."""
    return [stats.to_product_review_stats_row() for stats in parse_source_review_stats_rows(rows)]


def _sql_literal_list(values: Sequence[str]) -> str:
    if not values:
        raise ValueError("product_ids must not be empty")
    return ", ".join(sql_literal(str(value)) for value in values)


def _build_031_source_review_stats_sql(
    *,
    channel: str,
    product_ids_sql: str,
    source_key_type: str,
) -> str:
    product_expr = "TO_VARCHAR(dcpm.ecp_onln_prd_srno)"
    return f"""
WITH base AS (
    SELECT
        fprh.chn_cd AS source_channel,
        {product_expr} AS product_id,
        {sql_literal(source_key_type)} AS source_key_type,
        MAX(t4.ecp_onln_prd_nm) AS product_name,
        MAX(dpam.rprs_prd_nm) AS representative_product_name,
        MAX(dpam.brnd_cd) AS brand_id,
        MAX(dpam.brnd_nm) AS brand_name,
{_aggregate_sql()}
    FROM cdp.sf_cdpdw.f_prd_rv_hist fprh
    LEFT JOIN cdp.sf_cdpdw.d_prd_anl_mstr dpam
      ON fprh.prd_cd = dpam.prd_cd
    LEFT JOIN cdp.sf_cdpdw.d_chn_prd_mstr dcpm
      ON fprh.chn_cd = dcpm.chn_cd
     AND fprh.chn_prd_cd = dcpm.chn_prd_cd
    LEFT JOIN cdp.sf_cdpdw.d_ecp_onln_prd_mstr t4
      ON dcpm.chn_cd = t4.chn_cd
     AND dcpm.ecp_onln_prd_srno = t4.ecp_onln_prd_srno
    WHERE fprh.chn_cd = {sql_literal(channel)}
      AND {product_expr} IN ({product_ids_sql})
    GROUP BY fprh.chn_cd, {product_expr}
)
SELECT * FROM base
""".strip()


def _build_channel_product_review_stats_sql(
    *,
    channel: str,
    product_ids_sql: str,
    source_key_type: str,
) -> str:
    product_expr = "TO_VARCHAR(fprh.chn_prd_cd)"
    return f"""
WITH base AS (
    SELECT
        fprh.chn_cd AS source_channel,
        {product_expr} AS product_id,
        {sql_literal(source_key_type)} AS source_key_type,
        MAX(dpam.rprs_prd_nm) AS product_name,
        MAX(dpam.rprs_prd_nm) AS representative_product_name,
        MAX(dpam.brnd_cd) AS brand_id,
        MAX(dpam.brnd_nm) AS brand_name,
{_aggregate_sql()}
    FROM cdp.sf_cdpdw.f_prd_rv_hist fprh
    LEFT JOIN cdp.sf_cdpdw.d_prd_anl_mstr dpam
      ON fprh.prd_cd = dpam.prd_cd
    WHERE fprh.chn_cd = {sql_literal(channel)}
      AND {product_expr} IN ({product_ids_sql})
    GROUP BY fprh.chn_cd, {product_expr}
)
SELECT * FROM base
""".strip()


def _aggregate_sql() -> str:
    return f"""        COUNT(*) AS review_count_all,
        COUNT(fprh.prd_apal_scr) AS score_count_all,
        AVG(fprh.prd_apal_scr) AS avg_rating_all,
        MIN(fprh.stnd_ymd) AS review_min_date_all,
        MAX(fprh.stnd_ymd) AS review_max_date_all,
        COUNT(CASE
            WHEN {_SIX_MONTH_REVIEW_WINDOW}
            THEN 1
        END) AS review_count_6m,
        COUNT(CASE
            WHEN {_SIX_MONTH_REVIEW_WINDOW}
             AND fprh.prd_apal_scr IS NOT NULL
            THEN 1
        END) AS score_count_6m,
        AVG(CASE
            WHEN {_SIX_MONTH_REVIEW_WINDOW}
            THEN fprh.prd_apal_scr
        END) AS avg_rating_6m,
        MIN(CASE
            WHEN {_SIX_MONTH_REVIEW_WINDOW}
            THEN fprh.stnd_ymd
        END) AS review_min_date_6m,
        MAX(CASE
            WHEN {_SIX_MONTH_REVIEW_WINDOW}
            THEN fprh.stnd_ymd
        END) AS review_max_date_6m"""


def _get(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
        upper = name.upper()
        if upper in row:
            return row[upper]
    return None


def _get_required(row: Mapping[str, Any], name: str) -> Any:
    value = _get(row, name)
    if value is None:
        raise KeyError(name)
    return value


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) == 8 and stripped.isdigit():
            return datetime.strptime(stripped, "%Y%m%d").date()
        return date.fromisoformat(stripped[:10])
    raise TypeError(f"Unsupported date value type: {type(value).__name__}")


def _to_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
