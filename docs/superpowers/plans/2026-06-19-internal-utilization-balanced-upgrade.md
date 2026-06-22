# GraphRapping Internal Utilization Balanced Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade GraphRapping's own demo, recommendation, explanation, and frontend layers so they use the enriched final serving data without changing DB schema, DB load contracts, or AmoreSimulation-facing output.

**Architecture:** Keep the DB product/review-summary/source-review-stats pipeline read-only and unchanged. Inject the already-produced `source_review_stats` snapshot into the in-memory demo path, add small bounded source trust features to recommendation scoring, attach review summaries through optional read-only DB lookup, and expose the richer product payload in the frontend with clear separation between graph evidence and source review volume.

**Tech Stack:** Python 3.11, FastAPI, asyncpg, existing GraphRapping loaders/mart/rec modules, vanilla JS frontend, pytest.

---

## Decision And Boundaries

- Chosen approach: **Balanced recommendation upgrade**.
- Do not change:
  - `sql/ddl_raw.sql`
  - DB reload scripts
  - `product_review_stats` persistence shape
  - `serving_product_profile` schema
  - AmoreSimulation contract docs, except if a later doc-only note is explicitly requested
- Do change:
  - GraphRapping in-memory demo loading
  - GraphRapping recommendation scoring features
  - GraphRapping API response enrichment
  - GraphRapping frontend display
- Source review stats are **source trust / social proof**, not graph evidence.
- Review summaries stay in a sidecar and are not promoted into graph nodes or edges.
- Promoted-only serving remains the default recommendation/corpus contract.

## Current Measured Baseline

Local DB was measured on 2026-06-19:

- `product_master`: 517 active rows, 516 source-grounded, 1 collision product (`35119`).
- `product_review_stats`: 516 rows; 516 positive `source_review_count_6m`; 516 non-null `source_avg_rating_6m`; 0 zero 6-month counts.
- `serving_product_profile`: 517 rows; 516 source stats populated; 1 null source stats row for source-key collision.
- `serving_product_profile` vs `product_review_stats`: 0 mismatches.
- `review_summary_sidecar`: 516 rows; 495 normalized summaries; 21 not found; 1 collision excluded.
- In-memory `src.web.state.load_demo_data(...)` currently produces 517 serving products but 0 positive `source_review_count_6m`, because it does not load the source stats snapshot.
- Recommendation scorer currently shrinks by `review_count_all`, which means promoted graph evidence count, not source review count.

## File Structure

### Modify

- `src/web/state.py`
  - Load optional source review stats snapshot for demo/in-memory runs.
  - Pass `source_review_stats_by_product` into `run_batch`.

- `src/web/server.py`
  - Add request option for source stats snapshot path.
  - Add source stats counters to dashboard summary.
  - Add source trust metadata to recommendation responses.
  - Attach read-only review-summary sidecar data when DB is configured.

- `src/rec/scorer.py`
  - Add bounded source trust features.
  - Keep graph support shrinkage separate.

- `configs/scoring_weights.yaml`
  - Add low weights for source trust features.

- `src/rec/hook_generator.py`
  - Use product profile source stats only for auxiliary copy.

- `src/static/app.js`
  - Show source review stats, source rating, summary status, and trust chips.
  - Keep graph evidence count label separate.

- `src/static/index.html`
  - Minor UI copy/labels only if needed.

### Create

- `src/web/review_summary_sidecar.py`
  - Read-only DB helper for fetching summary sidecar rows by product id.
  - Fail closed to `None`/empty data when DB URL is absent or unavailable.

- `tests/test_web_state_source_stats.py`
  - In-memory demo source stats injection tests.

- `tests/test_source_trust_scoring.py`
  - Recommendation source popularity/rating tests.

- `tests/test_web_review_summary_sidecar.py`
  - Read-only sidecar helper and API shaping tests.

## Task 1: Inject Source Review Stats Into In-Memory Demo Runs

**Files:**
- Modify: `src/web/state.py`
- Modify: `src/web/server.py`
- Test: `tests/test_web_state_source_stats.py`

- [ ] **Step 1: Write failing test for source stats snapshot injection**

Create `tests/test_web_state_source_stats.py`:

```python
import json

from src.web.state import load_demo_data


def _product(product_id: str = "61289") -> dict:
    return {
        "ONLINE_PROD_SERIAL_NUMBER": product_id,
        "prd_nm": "블랙 쿠션",
        "BRAND_NAME": "헤라",
        "SOURCE_CHANNEL": "031",
        "SOURCE_KEY_TYPE": "ecp_onln_prd_srno",
        "SOURCE_TRUTH_QUALITY": "SOURCE_GROUNDED",
        "REPRESENTATIVE_PROD_NAME": "헤라 블랙 쿠션",
    }


def test_load_demo_data_applies_source_review_stats_snapshot(tmp_path):
    review_path = tmp_path / "reviews.json"
    review_path.write_text("[]", encoding="utf-8")

    stats_path = tmp_path / "source_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "product_id": "61289",
                        "source_product_id": "61289",
                        "source_channel": "031",
                        "source_key_type": "ecp_onln_prd_srno",
                        "source_review_count_6m": 862,
                        "source_review_score_count_6m": 862,
                        "source_avg_rating_6m": 4.941,
                        "source_review_min_date_6m": "2025-12-18",
                        "source_review_max_date_6m": "2026-06-17",
                        "source_review_count_all": 4965,
                        "source_review_score_count_all": 4965,
                        "source_avg_rating_all": 4.945,
                        "source_review_min_date_all": "2024-09-26",
                        "source_review_max_date_all": "2026-06-17",
                        "source": "snowflake:f_prd_rv_hist:test",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    state = load_demo_data(
        review_json_path=str(review_path),
        product_es_records=[_product()],
        user_profiles={},
        max_reviews=0,
        source="test",
        review_format="relation",
        kg_mode="off",
        source_review_stats_json_path=str(stats_path),
    )

    assert state.serving_products[0]["source_review_count_6m"] == 862
    assert state.serving_products[0]["source_avg_rating_6m"] == 4.941
    assert state.serving_products[0]["source_review_count_all"] == 4965
    assert state.batch_result["source_review_stats_by_product"]["61289"]["source_review_count_6m"] == 862
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
python -m pytest tests/test_web_state_source_stats.py -q
```

Expected before implementation:

```text
TypeError: load_demo_data() got an unexpected keyword argument 'source_review_stats_json_path'
```

- [ ] **Step 3: Add source stats loading to `src/web/state.py`**

Add imports:

```python
from pathlib import Path
from typing import Any
```

Add constants near `_MOCKDATA_DIR` equivalent in `src/web/state.py`:

```python
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SOURCE_REVIEW_STATS_PATH = (
    _PROJECT_ROOT / "data/source_snapshots/product_review_stats_snowflake_latest.json"
)
```

Extend `load_demo_data` signature:

```python
def load_demo_data(
    review_json_path: str,
    product_es_records: list[dict],
    user_profiles: dict[str, dict],
    max_reviews: int = 100,
    source: str = "demo",
    review_format: str = "relation",
    *,
    purchase_events_by_user: dict[str, list] | None = None,
    kg_mode: str | None = None,
    source_review_stats_by_product: dict[str, dict[str, Any]] | None = None,
    source_review_stats_json_path: str | None = str(_DEFAULT_SOURCE_REVIEW_STATS_PATH),
) -> DemoState:
```

Inside `load_demo_data`, after products are loaded and before `run_batch`, load and filter stats:

```python
    source_review_stats = _resolve_demo_source_review_stats(
        source_review_stats_by_product=source_review_stats_by_product,
        source_review_stats_json_path=source_review_stats_json_path,
        product_ids=set(product_result.product_masters),
    )
```

Pass stats into `run_batch`:

```python
        source_review_stats_by_product=source_review_stats,
```

Add helper at module bottom:

```python
def _resolve_demo_source_review_stats(
    *,
    source_review_stats_by_product: dict[str, dict[str, Any]] | None,
    source_review_stats_json_path: str | None,
    product_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if source_review_stats_by_product is not None:
        return {
            str(pid): row
            for pid, row in source_review_stats_by_product.items()
            if str(pid) in product_ids
        }
    if source_review_stats_json_path is None:
        return {}

    path = Path(source_review_stats_json_path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists():
        if path == _DEFAULT_SOURCE_REVIEW_STATS_PATH:
            return {}
        raise FileNotFoundError(f"source review stats snapshot not found: {path}")

    from src.loaders.source_review_stats_loader import load_source_review_stats_snapshot

    stats = load_source_review_stats_snapshot(path)
    return {pid: row for pid, row in stats.items() if pid in product_ids}
```

- [ ] **Step 4: Wire API request option in `src/web/server.py`**

Extend `PipelineRunRequest`:

```python
class PipelineRunRequest(BaseModel):
    review_json_path: str = _DEFAULT_REVIEW_PATH
    max_reviews: int = 5000
    source: str = "demo"
    review_format: str = "relation"
    source_review_stats_json_path: str | None = str(
        _PROJECT_ROOT / "data/source_snapshots/product_review_stats_snowflake_latest.json"
    )
```

Pass it into `load_demo_data`:

```python
        source_review_stats_json_path=req.source_review_stats_json_path,
```

Add source stats counters to `/api/dashboard/summary`:

```python
    source_stats_positive = sum(
        1 for p in demo_state.serving_products
        if (p.get("source_review_count_6m") or 0) > 0
    )
    source_rating_present = sum(
        1 for p in demo_state.serving_products
        if p.get("source_avg_rating_6m") is not None
    )
```

Return:

```python
        "source_review_stats_products": source_stats_positive,
        "source_avg_rating_products": source_rating_present,
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m pytest tests/test_web_state_source_stats.py -q
```

Expected:

```text
1 passed
```

## Task 2: Add Bounded Source Trust Scoring Features

**Files:**
- Modify: `src/rec/scorer.py`
- Modify: `configs/scoring_weights.yaml`
- Test: `tests/test_source_trust_scoring.py`

- [ ] **Step 1: Write failing source trust scoring tests**

Create `tests/test_source_trust_scoring.py`:

```python
import pytest

from src.rec.scorer import Scorer


def test_source_popularity_score_is_bounded_and_log_scaled():
    scorer = Scorer()
    scorer.load_from_dict(
        {
            "source_popularity_score": 1.0,
            "source_rating_score": 0.0,
        },
        shrinkage_k=10,
    )

    low = scorer.score({}, {"product_id": "low", "source_review_count_6m": 5, "review_count_all": 100}, [])
    high = scorer.score({}, {"product_id": "high", "source_review_count_6m": 5000, "review_count_all": 100}, [])

    assert high.raw_score > low.raw_score
    assert high.raw_score <= 1.0
    assert high.feature_contributions["source_popularity_score"] <= 1.0


def test_source_rating_score_rewards_high_recent_rating_only():
    scorer = Scorer()
    scorer.load_from_dict(
        {
            "source_popularity_score": 0.0,
            "source_rating_score": 1.0,
        },
        shrinkage_k=10,
    )

    low = scorer.score({}, {"product_id": "low", "source_avg_rating_6m": 3.9, "review_count_all": 100}, [])
    high = scorer.score({}, {"product_id": "high", "source_avg_rating_6m": 4.8, "review_count_all": 100}, [])

    assert low.raw_score == pytest.approx(0.0)
    assert high.raw_score == pytest.approx(0.8)


def test_source_trust_does_not_replace_graph_support_shrinkage():
    scorer = Scorer()
    scorer.load_from_dict({"source_popularity_score": 1.0}, shrinkage_k=10)

    no_graph_support = scorer.score(
        {},
        {"product_id": "p1", "source_review_count_6m": 5000, "review_count_all": 0},
        [],
    )
    graph_supported = scorer.score(
        {},
        {"product_id": "p2", "source_review_count_6m": 5000, "review_count_all": 100},
        [],
    )

    assert no_graph_support.raw_score == graph_supported.raw_score
    assert no_graph_support.shrinked_score < graph_supported.shrinked_score
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
python -m pytest tests/test_source_trust_scoring.py -q
```

Expected before implementation:

```text
KeyError: 'source_popularity_score'
```

or assertion failure because the scorer ignores the new weights.

- [ ] **Step 3: Extend scorer feature keys**

In `src/rec/scorer.py`, update `SCORING_FEATURE_KEYS`:

```python
    "source_popularity_score",
    "source_rating_score",
```

Add import:

```python
import math
```

Add helpers near `_freshness_score`:

```python
def _source_popularity_score(product: dict) -> float:
    count = product.get("source_review_count_6m")
    if count is None:
        count = product.get("source_review_count_all")
    try:
        count_int = int(count or 0)
    except (TypeError, ValueError):
        count_int = 0
    if count_int <= 0:
        return 0.0
    cap = 1000
    return min(math.log1p(count_int) / math.log1p(cap), 1.0)


def _source_rating_score(product: dict) -> float:
    rating = product.get("source_avg_rating_6m")
    if rating is None:
        rating = product.get("source_avg_rating_all")
    try:
        rating_float = float(rating)
    except (TypeError, ValueError):
        return 0.0
    if rating_float < 4.0:
        return 0.0
    return min(max(rating_float - 4.0, 0.0), 1.0)
```

Add to `features` inside `Scorer.score`:

```python
            "source_popularity_score": _source_popularity_score(product_profile),
            "source_rating_score": _source_rating_score(product_profile),
```

Do not change:

```python
        support_count = product_profile.get("review_count_all", 0) or 0
        shrinkage = support_count / (support_count + self._shrinkage_k) if support_count > 0 else 0.1
```

- [ ] **Step 4: Add low default weights**

In `configs/scoring_weights.yaml`, adjust the feature weights:

```yaml
  keyword_match: 0.16
  residual_bee_attr_match: 0.07
  context_match: 0.08
  concern_fit: 0.08
  concern_bridge_fit: 0.04
  ingredient_match: 0.07
  brand_match_conf_weighted: 0.06
  goal_fit_master: 0.05
  category_affinity: 0.05
  freshness_boost: 0.04
  source_popularity_score: 0.03
  source_rating_score: 0.02
```

Leave the personalization/co-use weights unchanged.

- [ ] **Step 5: Run focused scorer tests**

Run:

```bash
python -m pytest tests/test_source_trust_scoring.py tests/test_recommendation.py tests/test_recommendation_contract_consistency.py -q
```

Expected:

```text
all selected tests pass
```

## Task 3: Add Read-Only Review Summary Sidecar Access

**Files:**
- Create: `src/web/review_summary_sidecar.py`
- Modify: `src/web/server.py`
- Test: `tests/test_web_review_summary_sidecar.py`

- [ ] **Step 1: Write pure shaping tests**

Create `tests/test_web_review_summary_sidecar.py`:

```python
from src.web.review_summary_sidecar import summarize_sidecar_row


def test_summarize_sidecar_row_returns_safe_preview():
    row = {
        "product_id": "61289",
        "match_status": "exact_category",
        "normalized_summary": {
            "long": {
                "summary": "롱 요약입니다.",
                "review_cnt": 4965,
                "An_date": "2026-06-17",
            },
            "short": {
                "summary": "짧은 요약입니다.",
                "review_cnt": 4965,
            },
        },
        "an_date": "2026-06-17",
    }

    preview = summarize_sidecar_row(row)

    assert preview == {
        "product_id": "61289",
        "match_status": "exact_category",
        "an_date": "2026-06-17",
        "long_summary": "롱 요약입니다.",
        "short_summary": "짧은 요약입니다.",
        "review_count": 4965,
    }
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
python -m pytest tests/test_web_review_summary_sidecar.py -q
```

Expected before implementation:

```text
ModuleNotFoundError: No module named 'src.web.review_summary_sidecar'
```

- [ ] **Step 3: Implement read-only helper**

Create `src/web/review_summary_sidecar.py`:

```python
from __future__ import annotations

from typing import Any

import asyncpg

from src.db.connection import resolve_database_url


def summarize_sidecar_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    normalized = row.get("normalized_summary") or {}
    long_doc = normalized.get("long") or {}
    short_doc = normalized.get("short") or {}
    review_count = long_doc.get("review_cnt")
    if review_count is None:
        review_count = short_doc.get("review_cnt")
    return {
        "product_id": row.get("product_id"),
        "match_status": row.get("match_status"),
        "an_date": row.get("an_date"),
        "long_summary": long_doc.get("summary"),
        "short_summary": short_doc.get("summary"),
        "review_count": review_count,
    }


async def fetch_sidecar_summaries(
    product_ids: list[str],
    *,
    database_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not product_ids:
        return {}
    try:
        dsn = resolve_database_url(database_url)
    except RuntimeError:
        return {}

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, command_timeout=10)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT product_id, match_status, normalized_summary, an_date
                FROM review_summary_sidecar
                WHERE product_id = ANY($1::text[])
                """,
                product_ids,
            )
    except Exception:
        return {}
    finally:
        await pool.close()

    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        preview = summarize_sidecar_row(dict(row))
        if preview is not None:
            summaries[str(row["product_id"])] = preview
    return summaries
```

- [ ] **Step 4: Attach summary previews to product and recommendation APIs**

In `src/web/server.py`, import:

```python
from src.web.review_summary_sidecar import fetch_sidecar_summaries
```

In `get_product`, after `product` is found:

```python
    summaries = await fetch_sidecar_summaries([product_id])
```

Return:

```python
    return {
        "serving_profile": product,
        "master": master,
        "concept_links": links,
        "review_summary": summaries.get(product_id),
    }
```

In `recommend`, before building `results`, preload top candidate summaries after rerank:

```python
    summary_by_product = await fetch_sidecar_summaries([r.product_id for r in reranked])
```

Add to each result:

```python
                "review_summary": summary_by_product.get(r.product_id),
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m pytest tests/test_web_review_summary_sidecar.py -q
```

Expected:

```text
1 passed
```

## Task 4: Enrich Recommendation Response And Hooks

**Files:**
- Modify: `src/web/server.py`
- Modify: `src/rec/hook_generator.py`
- Test: `tests/test_recommendation.py`

- [ ] **Step 1: Write hook test for source trust copy**

Append this method to the existing `TestHookGenerator` class in
`tests/test_recommendation.py`:

```python
    def test_hooks_include_source_trust_when_product_profile_has_recent_volume(self):
        from src.rec.explainer import Explanation
        from src.rec.hook_generator import generate_hooks

        hooks = generate_hooks(
            Explanation(product_id="P1", paths=[], summary_ko=""),
            product_profile={
                "source_review_count_6m": 1200,
                "source_avg_rating_6m": 4.8,
            },
        )

        assert "최근 리뷰" in hooks.conversion
        assert "4.8" in hooks.conversion
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
python -m pytest tests/test_recommendation.py::TestHookGenerator::test_hooks_include_source_trust_when_product_profile_has_recent_volume -q
```

Expected before implementation: assertion failure because `product_profile` is ignored.

- [ ] **Step 3: Implement source-aware hook copy**

In `src/rec/hook_generator.py`, after base conversion is built:

```python
    source_count = _int_or_zero((product_profile or {}).get("source_review_count_6m"))
    source_rating = _float_or_none((product_profile or {}).get("source_avg_rating_6m"))
    if source_count >= 500 and source_rating is not None and source_rating >= 4.5:
        conversion = f"최근 리뷰 {source_count:,}건, 평균 {source_rating:.1f}점으로 반응이 안정적인 편이에요"
    elif source_count >= 500:
        conversion = f"최근 리뷰 {source_count:,}건으로 충분히 검증된 편이에요"
```

Add helpers:

```python
def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Pass product profile into hook generation**

In `src/web/server.py`, change:

```python
            hooks = generate_hooks(exp)
```

to:

```python
            hooks = generate_hooks(exp, product_profile=p)
```

Add source trust metadata to recommendation result:

```python
                "source_trust": {
                    "review_count_6m": p.get("source_review_count_6m"),
                    "avg_rating_6m": p.get("source_avg_rating_6m"),
                    "review_count_all": p.get("source_review_count_all"),
                    "avg_rating_all": p.get("source_avg_rating_all"),
                },
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m pytest tests/test_recommendation.py -q
```

Expected:

```text
all recommendation tests pass
```

## Task 5: Frontend Surface Upgrade

**Files:**
- Modify: `src/static/app.js`
- Modify: `src/static/index.html` only if labels need stable containers
- Test: manual browser smoke plus `python -m pytest` backend tests

- [ ] **Step 1: Add frontend formatting helpers**

In `src/static/app.js`, near `productDisplayName`, add:

```javascript
function fmtCount(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
  return Number(v).toLocaleString('ko-KR');
}

function fmtRating(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '-';
  return Number(v).toFixed(2);
}

function summaryStatusLabel(summary) {
  if (!summary) return '요약 없음';
  if (summary.match_status === 'exact_category') return '요약 있음';
  return summary.match_status || '요약 있음';
}
```

- [ ] **Step 2: Update dashboard KPI rendering**

In `renderKPI`, add cards:

```javascript
    <div class="kpi-card"><div class="label">원천 리뷰통계</div><div class="value blue">${d.source_review_stats_products || 0}</div></div>
    <div class="kpi-card"><div class="label">원천 평점</div><div class="value green">${d.source_avg_rating_products || 0}</div></div>
```

- [ ] **Step 3: Update product table columns**

Change `loadProducts()` table header to:

```javascript
    <th>상품명</th><th>브랜드</th><th>카테고리</th><th>원천 6M 리뷰</th><th>원천 평점</th><th>그래프 근거</th><th>Top BEE</th>
```

Change row cells:

```javascript
      <td>${fmtCount(p.source_review_count_6m)}</td>
      <td>${fmtRating(p.source_avg_rating_6m)}</td>
      <td>${p.review_count_all || 0}</td>
```

- [ ] **Step 4: Update product detail display**

In `showProductDetail`, replace the raw-only pre block with a summary section followed by JSON:

```javascript
  const summary = d.review_summary;
  dp.innerHTML = `
    <h3>상품 상세: ${name}</h3>
    <div class="kpi-grid">
      <div class="kpi-card"><div class="label">원천 6M 리뷰</div><div class="value blue">${fmtCount(sp.source_review_count_6m)}</div></div>
      <div class="kpi-card"><div class="label">원천 6M 평점</div><div class="value green">${fmtRating(sp.source_avg_rating_6m)}</div></div>
      <div class="kpi-card"><div class="label">그래프 근거 리뷰</div><div class="value">${sp.review_count_all || 0}</div></div>
      <div class="kpi-card"><div class="label">리뷰 요약</div><div class="value" style="font-size:13px">${summaryStatusLabel(summary)}</div></div>
    </div>
    ${summary ? `<div class="panel" style="margin-top:12px">
      <h2>리뷰 요약</h2>
      <p>${summary.short_summary || summary.long_summary || '-'}</p>
    </div>` : ''}
    <pre>${JSON.stringify(d, null, 2)}</pre>
  `;
```

- [ ] **Step 5: Update recommendation cards**

In `renderRecommendResults`, under score line add:

```javascript
        <div class="hooks" style="margin-top:8px">
          <span><span class="label">원천 6M 리뷰:</span> ${fmtCount(r.source_trust && r.source_trust.review_count_6m)}</span>
          <span><span class="label">원천 평점:</span> ${fmtRating(r.source_trust && r.source_trust.avg_rating_6m)}</span>
          <span><span class="label">요약:</span> ${summaryStatusLabel(r.review_summary)}</span>
        </div>
```

- [ ] **Step 6: Manual UI smoke**

Run dev server:

```bash
GRAPHRAPPING_ENABLE_PIPELINE_RUN=1 uvicorn src.web.server:app --reload --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Smoke checklist:

- Pipeline run with 906 reviews completes.
- Dashboard shows source stats products > 0.
- Product table shows source 6M review and rating columns.
- Recommendation cards show source trust chips.
- Product detail fetches review summary when DB URL is configured; absence of DB does not break page.

## Task 6: Contract And Regression Verification

**Files:**
- No code files unless tests expose a bug.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
python -m pytest \
  tests/test_web_state_source_stats.py \
  tests/test_source_trust_scoring.py \
  tests/test_web_review_summary_sidecar.py \
  tests/test_recommendation.py \
  tests/test_recommendation_contract_consistency.py \
  tests/test_serving_uses_promoted_only.py \
  tests/test_corpus_promotion_baseline.py \
  -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Run lint**

Run:

```bash
ruff check .
```

Expected:

```text
All checks passed!
```

- [ ] **Step 3: Run full tests**

Run:

```bash
python -m pytest -q
```

Expected:

```text
679+ passed, existing skips only
```

The exact pass count can rise because this plan adds tests.

- [ ] **Step 4: Run DB contract validator without changing DB**

Run:

```bash
python - <<'PY'
import asyncio
import json
import asyncpg
from src.db.contract_validator import validate_all

DSN = "postgresql://postgres:postgres@localhost:5432/graphrapping"

async def main():
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    try:
        result = await validate_all(
            pool,
            expected_min_source_review_count_6m=516,
            expected_min_source_avg_rating_6m=516,
            enforce_source_grounding=True,
        )
        print(json.dumps({"status": result.status.value, "counts": dict(result.counts)}, ensure_ascii=False, indent=2))
    finally:
        await pool.close()

asyncio.run(main())
PY
```

Expected:

```json
{
  "status": "OK"
}
```

- [ ] **Step 5: Confirm no DB schema/load files changed**

Run:

```bash
git diff -- sql/ddl_raw.sql src/jobs/run_full_load_db.py src/db/repos/product_repo.py
```

Expected:

```text
no diff for DB schema/load contract files in this task
```

## Execution Notes

- Use separate commits by layer:
  1. `feat: load source stats in web demo path`
  2. `feat: add source trust recommendation features`
  3. `feat: expose summary sidecar in web APIs`
  4. `feat: enrich demo UI product and recommendation views`
- If a test failure repeats twice with the same root cause, add an `ERR_HIST/YYYY-MM-DD_HHmm_<summary>.md` entry before continuing.
- If implementation reveals a need to change DB schema/load behavior, stop and ask the user first. That is explicitly out of scope for this plan.

## Self-Review

- Spec coverage: The plan covers source stats injection, source trust scoring, summary sidecar read-only usage, frontend display, and verification.
- Placeholder scan: No TBD/TODO placeholders remain.
- Type consistency: New fields use existing serving profile names (`source_review_count_6m`, `source_avg_rating_6m`, `source_review_count_all`, `source_avg_rating_all`).
- Scope check: DB writes, DDL, and AmoreSimulation contract changes are excluded.
