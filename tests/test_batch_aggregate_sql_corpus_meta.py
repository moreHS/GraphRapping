"""
P3-6/P3-7 (Wave 3.9): `batch_aggregate_product_signals_sql` must populate
`distinct_review_count`, `avg_confidence`, and `synthetic_ratio` itself so
downstream Python post-processing doesn't need to re-read wrapped_signal.

Contract tests (skip-proof) verify the SQL string shape. Behavioral
verification against real Postgres lives in `test_postgres_integration.py`.
"""

from __future__ import annotations

import inspect

from src.db.repos import mart_repo


def test_function_exists() -> None:
    assert callable(mart_repo.batch_aggregate_product_signals_sql)
    assert inspect.iscoroutinefunction(mart_repo.batch_aggregate_product_signals_sql)


def test_insert_column_list_includes_corpus_meta() -> None:
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    for col in ("distinct_review_count", "avg_confidence", "synthetic_ratio"):
        assert col in src, f"corpus meta column {col!r} missing from SQL"


def test_select_computes_avg_source_confidence() -> None:
    """P3-6: avg_confidence MUST come from `AVG(source_confidence)` not weight."""
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    assert "AVG(source_confidence)" in src
    # Negative: must not aggregate weight as confidence
    assert "AVG(weight)" not in src


def test_select_computes_synthetic_ratio_from_evidence_kind() -> None:
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    assert "evidence_kind = 'BEE_SYNTHETIC'" in src
    # Ratio = synthetic count / total — denominator is COUNT(*) per group
    assert "synthetic_ratio" in src


def test_do_update_set_includes_corpus_meta_for_revival() -> None:
    """On re-aggregation conflict, the corpus meta fields must refresh
    (so an out-of-date row picks up new counts)."""
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    # The DO UPDATE SET clause should refresh all three
    do_update_idx = src.find("DO UPDATE SET")
    assert do_update_idx != -1
    do_update_body = src[do_update_idx:]
    for col in ("distinct_review_count", "avg_confidence", "synthetic_ratio"):
        assert f"{col} = EXCLUDED.{col}" in do_update_body, (
            f"{col} missing from DO UPDATE SET — stale row would keep old value"
        )


def test_is_promoted_and_corpus_weight_documented_as_python_post_step() -> None:
    """The docstring/comments must explain why is_promoted and corpus_weight
    are deferred to a Python post-step (window-aware threshold logic)."""
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    assert "is_promoted" in src
    assert "corpus_weight" in src
    # The NOTE/comment block must surface in source
    assert "post-hoc Python" in src or "Python step" in src


def test_grouping_unchanged() -> None:
    """Sanity: GROUP BY must still aggregate at (product, edge, dst) granularity."""
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    assert "GROUP BY target_product_id, edge_type, dst_type, dst_id" in src


def test_catalog_validation_still_filtered() -> None:
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    assert "signal_family != 'CATALOG_VALIDATION'" in src


def test_distinct_review_count_excludes_empty_review_id() -> None:
    """P3-7 (Wave 3.9): Python aggregator excludes empty/None review_ids from
    distinct counts; the SQL path must do the same via `NULLIF(review_id, '')`
    so the two paths cannot diverge on legacy rows with blank review_id.
    """
    src = inspect.getsource(mart_repo.batch_aggregate_product_signals_sql)
    occurrences = src.count("COUNT(DISTINCT NULLIF(review_id, ''))")
    assert occurrences >= 2, (
        f"Expected COUNT(DISTINCT NULLIF(review_id, '')) in both review_cnt "
        f"and distinct_review_count positions; got {occurrences}."
    )
    # Negative: bare COUNT(DISTINCT review_id) (without NULLIF) must not remain.
    assert "COUNT(DISTINCT review_id)" not in src, (
        "Plain COUNT(DISTINCT review_id) still present — would count empty "
        "string as a distinct value, diverging from Python."
    )
