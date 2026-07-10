"""
Phase 2.1: serving-mode wiring in src/web/server.py.

- ``extract_id`` and ``DemoServingStore`` (live-state read) contract.
- Env-driven mode/refresh resolution + ``get_serving_store`` behaviour.
- Demo-mode default keeps reading the module-level ``demo_state`` (regression).
- DB-mode ``/api/recommend`` end-to-end: store + wrapped_signal fetch + batched
  ``DBProvenanceProvider`` attach review snippets, with ``demo_state`` unloaded.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from src.web import server
from src.rec.provenance_provider import InMemoryProvenanceProvider
from src.web.serving_store import DemoServingStore, extract_id
from src.web.state import DemoState


# ---------------------------------------------------------------------------
# extract_id + DemoServingStore
# ---------------------------------------------------------------------------


def test_extract_id_handles_str_and_dict_and_junk() -> None:
    assert extract_id("keyword:a") == "keyword:a"
    assert extract_id({"id": "keyword:b", "score": 3}) == "keyword:b"
    assert extract_id({"score": 3}) is None
    assert extract_id({"id": 5}) is None  # non-string id
    assert extract_id(42) is None


@pytest.mark.asyncio
async def test_demo_store_reads_live_state_rebind() -> None:
    holder: dict[str, DemoState] = {"state": DemoState(loaded=True)}
    holder["state"].serving_products = [{"product_id": "p1"}]
    holder["state"].serving_users = [{"user_id": "u1"}]
    store = DemoServingStore(lambda: holder["state"])

    assert [p["product_id"] for p in await store.get_products()] == ["p1"]
    assert (await store.get_product("p1")) == {"product_id": "p1"}
    assert await store.get_product("missing") is None
    assert (await store.get_user("u1")) == {"user_id": "u1"}

    # Rebinding the provided state is reflected (pipeline reload / test monkeypatch).
    holder["state"] = DemoState(loaded=True)
    holder["state"].serving_products = [{"product_id": "p2"}]
    assert [p["product_id"] for p in await store.get_products()] == ["p2"]


# ---------------------------------------------------------------------------
# mode / refresh resolution
# ---------------------------------------------------------------------------


def test_serving_mode_defaults_to_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    assert server._serving_mode() == "demo"
    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "DB")
    assert server._serving_mode() == "db"


def test_serving_refresh_sec_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_REFRESH_SEC", raising=False)
    assert server._serving_refresh_sec() == 300.0
    monkeypatch.setenv("GRAPHRAPPING_SERVING_REFRESH_SEC", "45")
    assert server._serving_refresh_sec() == 45.0
    monkeypatch.setenv("GRAPHRAPPING_SERVING_REFRESH_SEC", "not-a-number")
    assert server._serving_refresh_sec() == 300.0


def test_get_serving_store_demo_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    store = server.get_serving_store()
    assert isinstance(store, DemoServingStore)


def test_get_serving_store_db_without_init_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "db")
    monkeypatch.setattr(server, "_serving_store", None)
    with pytest.raises(RuntimeError, match="not initialized"):
        server.get_serving_store()


# ---------------------------------------------------------------------------
# DB-mode /api/recommend end-to-end (fake store + fake provenance pool)
# ---------------------------------------------------------------------------


def _source_rich_product() -> dict[str, Any]:
    return {
        "product_id": "P1",
        "brand_name": "헤라",
        "representative_product_name": "블랙 쿠션",
        "brand_id": "brand_hera",
        "brand_concept_ids": ["brand_hera"],
        "category_id": "cat_cushion",
        "category_name": "쿠션",
        "category_concept_ids": ["cat_cushion"],
        "ingredient_concept_ids": [],
        "main_benefit_concept_ids": [],
        "top_keyword_ids": [{"id": "kw_thin_spread", "score": 0.9, "review_cnt": 40}],
        "top_bee_attr_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_coused_product_ids": [],
        "top_comparison_product_ids": [],
        "review_count_all": 120,
        "source_review_count_6m": 1200,
        "source_avg_rating_6m": 4.8,
    }


def _user() -> dict[str, Any]:
    return {
        "user_id": "U1",
        "preferred_brand_ids": [{"id": "brand_hera", "weight": 1.0}],
        "preferred_category_ids": [{"id": "cat_cushion", "weight": 1.0}],
        "preferred_keyword_ids": [{"id": "kw_thin_spread", "weight": 1.0}],
    }


class _FakeStore:
    def __init__(self, products: list[dict], users: list[dict]) -> None:
        self._products = products
        self._users = users

    async def get_products(self) -> list[dict]:
        return self._products

    async def get_product(self, product_id: str) -> dict | None:
        return next((p for p in self._products if p["product_id"] == product_id), None)

    async def get_users(self) -> list[dict]:
        return self._users

    async def get_user(self, user_id: str) -> dict | None:
        return next((u for u in self._users if u["user_id"] == user_id), None)


class _ProvAcquireCtx:
    def __init__(self, conn: "_ProvConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_ProvConn":
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _ProvPool:
    def __init__(self, conn: "_ProvConn") -> None:
        self._conn = conn

    def acquire(self) -> _ProvAcquireCtx:
        return _ProvAcquireCtx(self._conn)


class _ProvConn:
    """Serves the wrapped_signal → signal_evidence → fact_provenance chain for
    the DB-mode provenance path. keyword_id 'kw_thin_spread' matches the
    explanation path's concept_id after normalization."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        if "FROM wrapped_signal" in query:
            self.calls.append("wrapped_signal")
            return [
                {"signal_id": "s1", "target_product_id": "P1", "dst_id": "kw_thin_spread",
                 "keyword_id": "kw_thin_spread", "bee_attr_id": None},
            ]
        if "FROM signal_evidence" in query:
            self.calls.append("signal_evidence")
            return [{"signal_id": "s1", "fact_id": "fact_1", "evidence_rank": 0}]
        if "FROM fact_provenance" in query:
            self.calls.append("fact_provenance")
            return [{"fact_id": "fact_1", "snippet": "커버력이 좋아요", "review_id": "r1",
                     "start_offset": None, "end_offset": None}]
        if "FROM review_raw" in query:
            self.calls.append("review_raw")
            return []
        raise AssertionError(f"unexpected query: {query!r}")


@pytest.mark.asyncio
async def test_db_mode_recommend_attaches_provenance_snippets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "db")
    # demo_state stays UNLOADED — proves DB mode does not depend on a pipeline run.
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))
    monkeypatch.setattr(server, "_serving_store", _FakeStore([_source_rich_product()], [_user()]))

    prov_conn = _ProvConn()

    async def _fake_get_pool() -> Any:
        return _ProvPool(prov_conn)

    # _build_provenance_context imports get_pool from src.db.connection at call time.
    monkeypatch.setattr("src.db.connection.get_pool", _fake_get_pool)

    async def _fake_fetch(product_ids: list[str]) -> dict:
        return {pid: None for pid in product_ids}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _fake_fetch)

    payload = await server.recommend(server.RecommendRequest(user_id="U1", top_k=1))

    result = payload["results"][0]
    assert result["product_id"] == "P1"
    keyword_paths = [
        p for p in result["explanation_paths"]
        if p["type"] == "keyword" and p["snippets"]
    ]
    assert keyword_paths, "DB-mode recommend must attach a provenance snippet"
    assert keyword_paths[0]["snippets"][0] == {"review_id": "r1", "text": "커버력이 좋아요"}
    # The provenance chain was pulled in one batch per layer (no N+1).
    assert prov_conn.calls.count("signal_evidence") == 1
    assert prov_conn.calls.count("fact_provenance") == 1
    assert prov_conn.calls.count("wrapped_signal") == 1


@pytest.mark.asyncio
async def test_demo_mode_recommend_attaches_provenance_snippets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the batched demo path: with an in-memory provider
    and a product signal whose keyword anchor matches the keyword path, the
    recommendation attaches the review snippet (mode defaults to demo)."""
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)

    state = DemoState(loaded=True)
    state.serving_products = [_source_rich_product()]
    state.serving_users = [_user()]
    # Raw product signal whose keyword anchor normalizes to the keyword path's
    # concept_id ("kw_thin_spread"), so signal_ids_by_concept_path links them.
    state.product_signals = {
        "P1": [{"signal_id": "s1", "dst_id": "kw_thin_spread",
                "keyword_id": "kw_thin_spread", "bee_attr_id": None}]
    }
    state.provenance_provider = InMemoryProvenanceProvider(
        signal_evidence_by_signal={"s1": [{"signal_id": "s1", "fact_id": "fact_1", "evidence_rank": 0}]},
        fact_provenance_by_fact={
            "fact_1": [{"fact_id": "fact_1", "snippet": "가볍게 발려요", "review_id": "r1",
                        "start_offset": None, "end_offset": None}]
        },
        review_text_by_id={"r1": "가볍게 발려요"},
    )
    monkeypatch.setattr(server, "demo_state", state)

    async def _fake_fetch(product_ids: list[str]) -> dict:
        return {pid: None for pid in product_ids}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", _fake_fetch)

    payload = await server.recommend(server.RecommendRequest(user_id="U1", top_k=1))

    result = payload["results"][0]
    keyword_paths = [
        p for p in result["explanation_paths"]
        if p["type"] == "keyword" and p["snippets"]
    ]
    assert keyword_paths, "demo-mode recommend must attach provenance snippets when signals match"
    assert keyword_paths[0]["snippets"][0] == {"review_id": "r1", "text": "가볍게 발려요"}


# ---------------------------------------------------------------------------
# /api/dashboard/summary — mode-aware guard (demo + db)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_summary_demo_mode_reports_pipeline_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    state = DemoState(loaded=True)
    state.review_count = 42
    state.batch_result = {"total_signals": 7}
    state.quarantine_stats = {"quarantine_placeholder": 3}
    state.serving_products = [
        {"product_id": "p1", "source_review_count_6m": 10, "source_avg_rating_6m": 4.5},
        {"product_id": "p2", "source_review_count_6m": 0, "source_avg_rating_6m": None},
    ]
    state.serving_users = [{"user_id": "u1"}]
    monkeypatch.setattr(server, "demo_state", state)

    payload = await server.dashboard_summary()

    assert payload["reviews_processed"] == 42
    assert payload["total_signals"] == 7
    assert payload["total_quarantined"] == 3
    assert payload["serving_products"] == 2
    assert payload["serving_users"] == 1
    assert payload["source_review_stats_products"] == 1  # only p1 positive
    assert payload["source_avg_rating_products"] == 1     # only p1 has a rating
    assert payload["loaded"] is True


@pytest.mark.asyncio
async def test_dashboard_summary_demo_mode_requires_pipeline_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GRAPHRAPPING_SERVING_MODE", raising=False)
    monkeypatch.setattr(server, "_serving_store", None)
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))
    with pytest.raises(HTTPException) as excinfo:
        await server.dashboard_summary()
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_dashboard_summary_db_mode_uses_store_and_zeroes_demo_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB mode must NOT 400 on an unloaded demo_state (the bug): serving counts
    come from the store, demo-pipeline stats fall back to 0."""
    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "db")
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))
    products = [
        {"product_id": "P1", "source_review_count_6m": 100, "source_avg_rating_6m": 4.8},
        {"product_id": "P2", "source_review_count_6m": 0, "source_avg_rating_6m": None},
    ]
    monkeypatch.setattr(server, "_serving_store", _FakeStore(products, [{"user_id": "U1"}]))

    payload = await server.dashboard_summary()

    assert payload["serving_products"] == 2
    assert payload["serving_users"] == 1
    # Demo-pipeline artifacts have no DB equivalent → 0.
    assert payload["reviews_processed"] == 0
    assert payload["total_signals"] == 0
    assert payload["total_quarantined"] == 0
    # Source-review stats are store-derived, so they populate in DB mode too.
    assert payload["source_review_stats_products"] == 1
    assert payload["source_avg_rating_products"] == 1
    # `loaded` must reflect DB-mode serving readiness, not the (unloaded) demo
    # pipeline state — this is the bug fix: it must not be hardcoded False.
    assert payload["loaded"] is True


# ---------------------------------------------------------------------------
# /api/graphs/product — evidence view is demo-only (fix: explicit 400 in db mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_product_graph_evidence_view_unsupported_in_db_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRAPHRAPPING_SERVING_MODE", "db")
    monkeypatch.setattr(server, "demo_state", DemoState(loaded=False))
    monkeypatch.setattr(server, "_serving_store", _FakeStore([_source_rich_product()], [_user()]))

    with pytest.raises(HTTPException) as excinfo:
        await server.product_graph("P1", view="evidence")
    assert excinfo.value.status_code == 400
    assert "evidence view" in str(excinfo.value.detail)

    # Corpus view (store-backed) still works in DB mode.
    graph = await server.product_graph("P1", view="corpus")
    assert any(node["id"] == "P1" for node in graph["nodes"])
    assert graph["view_mode"] == "corpus"
