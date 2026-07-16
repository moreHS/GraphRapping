"""
Phase 2.1 / 0.4: DB-backed provenance provider unit tests (fake asyncpg pool).

`src.rec.provenance_provider.DBProvenanceProvider` must:
- pull the whole signal_evidence → fact_provenance → review_raw chain for a
  *batch* of signal_ids in a fixed number of round-trips (no N+1), and
- preserve nullable `fact_provenance.review_id` so `ExplanationService` pairs
  each snippet with its own id (or None).

Also exercised end-to-end through `ExplanationService` to confirm the provider
is a drop-in for the demo `InMemoryProvenanceProvider`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.rec.explainer import ExplanationPath, ExplanationService, SnippetEvidence
from src.rec.provenance_provider import (
    DBProvenanceProvider,
    fetch_keyword_signal_triples,
    fetch_product_signals,
    signal_ids_by_concept_path,
)
from src.rec.scorer import ScoredProduct


# ---------------------------------------------------------------------------
# Fake asyncpg pool / connection that records every query issued
# ---------------------------------------------------------------------------


class _FakeAcquireCtx:
    def __init__(self, conn: "_RecordingConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_RecordingConn":
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _RecordingPool:
    def __init__(self, conn: "_RecordingConn") -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._conn)


class _RecordingConn:
    """Routes fetch by table substring and records (table, args) per call so
    tests can assert exactly how many round-trips happened."""

    def __init__(
        self,
        *,
        signal_evidence: dict[str, list[dict]],
        fact_provenance: dict[str, list[dict]],
        review_text: dict[str, str],
    ) -> None:
        self._signal_evidence = signal_evidence
        self._fact_provenance = fact_provenance
        self._review_text = review_text
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict]:
        if "FROM signal_evidence" in query:
            self.calls.append(("signal_evidence", args))
            wanted = set(args[0])
            rows: list[dict] = []
            for sid in args[0]:
                rows.extend(self._signal_evidence.get(sid, []))
            assert {r["signal_id"] for r in rows}.issubset(wanted)
            return rows
        if "FROM fact_provenance" in query:
            self.calls.append(("fact_provenance", args))
            rows = []
            for fid in args[0]:
                rows.extend(self._fact_provenance.get(fid, []))
            return rows
        if "FROM review_raw" in query:
            self.calls.append(("review_raw", args))
            return [
                {"review_id": rid, "review_text": self._review_text[rid]}
                for rid in args[0]
                if rid in self._review_text
            ]
        if "FROM wrapped_signal" in query:
            self.calls.append(("wrapped_signal", args))
            return []
        raise AssertionError(f"unexpected query: {query!r}")

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table, _args in self.calls:
            out[table] = out.get(table, 0) + 1
        return out


def _scored() -> ScoredProduct:
    return ScoredProduct(
        product_id="p1",
        raw_score=0.8,
        shrinked_score=0.75,
        final_score=0.75,
        feature_contributions={"keyword_match": 0.5},
    )


# ---------------------------------------------------------------------------
# prefetch batching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefetch_batches_chain_in_three_queries_regardless_of_fanout() -> None:
    # 3 signals → 3 facts → 3 reviews. A per-call provider would issue many
    # queries; the batch provider must use exactly one query per layer.
    signal_evidence = {
        f"sig_{i}": [{"signal_id": f"sig_{i}", "fact_id": f"fact_{i}", "evidence_rank": 0}]
        for i in range(3)
    }
    fact_provenance = {
        f"fact_{i}": [
            {
                "fact_id": f"fact_{i}",
                "snippet": None,  # forces the review_raw fallback
                "review_id": f"r_{i}",
                "start_offset": None,
                "end_offset": None,
            }
        ]
        for i in range(3)
    }
    review_text = {f"r_{i}": f"review text {i}" for i in range(3)}
    conn = _RecordingConn(
        signal_evidence=signal_evidence,
        fact_provenance=fact_provenance,
        review_text=review_text,
    )
    provider = DBProvenanceProvider(_RecordingPool(conn))

    await provider.prefetch([f"sig_{i}" for i in range(3)])

    # Exactly one round-trip per layer.
    assert conn.counts() == {"signal_evidence": 1, "fact_provenance": 1, "review_raw": 1}


@pytest.mark.asyncio
async def test_protocol_reads_hit_no_db_after_prefetch() -> None:
    conn = _RecordingConn(
        signal_evidence={"sig_1": [{"signal_id": "sig_1", "fact_id": "fact_1", "evidence_rank": 0}]},
        fact_provenance={"fact_1": [{"fact_id": "fact_1", "snippet": "stored snippet", "review_id": "r1"}]},
        review_text={},
    )
    provider = DBProvenanceProvider(_RecordingPool(conn))
    await provider.prefetch(["sig_1"])
    calls_after_prefetch = len(conn.calls)

    # These are the calls ExplanationService makes; none may touch the DB.
    ev = await provider.get_signal_evidence("sig_1")
    prov = await provider.get_fact_provenance("fact_1")
    snippet = await provider.get_review_snippet("r1", None, None)

    assert ev[0]["fact_id"] == "fact_1"
    assert prov[0]["snippet"] == "stored snippet"
    assert snippet is None  # r1 has a stored snippet, so no review text prefetched
    assert len(conn.calls) == calls_after_prefetch  # no new queries


@pytest.mark.asyncio
async def test_prefetch_is_incremental_and_idempotent() -> None:
    conn = _RecordingConn(
        signal_evidence={
            "sig_1": [{"signal_id": "sig_1", "fact_id": "fact_1", "evidence_rank": 0}],
            "sig_2": [{"signal_id": "sig_2", "fact_id": "fact_2", "evidence_rank": 0}],
        },
        fact_provenance={
            "fact_1": [{"fact_id": "fact_1", "snippet": "s1", "review_id": None}],
            "fact_2": [{"fact_id": "fact_2", "snippet": "s2", "review_id": None}],
        },
        review_text={},
    )
    provider = DBProvenanceProvider(_RecordingPool(conn))

    await provider.prefetch(["sig_1"])
    await provider.prefetch(["sig_1"])  # already cached → no query
    assert conn.counts().get("signal_evidence") == 1

    await provider.prefetch(["sig_1", "sig_2"])  # only sig_2 is new
    assert conn.counts().get("signal_evidence") == 2
    # sig_2's evidence is now visible.
    assert (await provider.get_signal_evidence("sig_2"))[0]["fact_id"] == "fact_2"


@pytest.mark.asyncio
async def test_empty_signal_batch_issues_no_query() -> None:
    conn = _RecordingConn(signal_evidence={}, fact_provenance={}, review_text={})
    provider = DBProvenanceProvider(_RecordingPool(conn))
    await provider.prefetch([])
    await provider.prefetch(["", None])  # type: ignore[list-item]
    assert conn.calls == []


# ---------------------------------------------------------------------------
# Nullable review_id — atomic snippet pairing through ExplanationService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nullable_review_id_preserved_through_explanation_service() -> None:
    # One fact, two provenance rows: first snippet has NO review_id (nullable
    # column), second has one. The DB provider must not mis-pair them.
    conn = _RecordingConn(
        signal_evidence={"sig_1": [{"signal_id": "sig_1", "fact_id": "fact_1", "evidence_rank": 0}]},
        fact_provenance={
            "fact_1": [
                {"fact_id": "fact_1", "snippet": "snippet without review", "review_id": None,
                 "start_offset": None, "end_offset": None},
                {"fact_id": "fact_1", "snippet": "snippet with review", "review_id": "r_present",
                 "start_offset": None, "end_offset": None},
            ]
        },
        review_text={},
    )
    provider = DBProvenanceProvider(_RecordingPool(conn))
    await provider.prefetch(["sig_1"])

    service = ExplanationService(provenance_provider=provider)
    result = await service.explain_with_provenance(
        scored=_scored(), overlap_concepts=["keyword:moisture"], signal_ids=["sig_1"],
    )

    path = next(p for p in result.provenance_paths if p.snippet_evidence)
    assert path.snippet_evidence == [
        SnippetEvidence(snippet="snippet without review", review_id=None),
        SnippetEvidence(snippet="snippet with review", review_id="r_present"),
    ]


@pytest.mark.asyncio
async def test_review_text_fallback_used_when_snippet_missing() -> None:
    conn = _RecordingConn(
        signal_evidence={"sig_1": [{"signal_id": "sig_1", "fact_id": "fact_1", "evidence_rank": 0}]},
        fact_provenance={
            "fact_1": [
                {"fact_id": "fact_1", "snippet": None, "review_id": "r1",
                 "start_offset": 0, "end_offset": 4},
            ]
        },
        review_text={"r1": "촉촉하고 좋아요"},
    )
    provider = DBProvenanceProvider(_RecordingPool(conn))
    await provider.prefetch(["sig_1"])

    service = ExplanationService(provenance_provider=provider)
    result = await service.explain_with_provenance(
        scored=_scored(), overlap_concepts=["keyword:moisture"], signal_ids=["sig_1"],
    )
    path = next(p for p in result.provenance_paths if p.snippet_evidence)
    # get_review_snippet slices review_text[0:4] → first 4 chars.
    assert path.snippet_evidence == [SnippetEvidence(snippet="촉촉하고", review_id="r1")]


# ---------------------------------------------------------------------------
# fetch_product_signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_product_signals_groups_by_product_in_one_query() -> None:
    class _SignalsConn(_RecordingConn):
        async def fetch(self, query: str, *args: Any) -> list[dict]:
            assert "FROM wrapped_signal" in query
            self.calls.append(("wrapped_signal", args))
            return [
                {"signal_id": "s1", "target_product_id": "p1", "dst_id": "keyword:a",
                 "keyword_id": "keyword:a", "bee_attr_id": None},
                {"signal_id": "s2", "target_product_id": "p1", "dst_id": "bee_attr:b",
                 "keyword_id": None, "bee_attr_id": "bee_attr:b"},
                {"signal_id": "s3", "target_product_id": "p2", "dst_id": "keyword:c",
                 "keyword_id": "keyword:c", "bee_attr_id": None},
            ]

    conn = _SignalsConn(signal_evidence={}, fact_provenance={}, review_text={})
    result = await fetch_product_signals(_RecordingPool(conn), ["p1", "p2"])

    assert set(result) == {"p1", "p2"}
    assert [s["signal_id"] for s in result["p1"]] == ["s1", "s2"]
    assert [s["signal_id"] for s in result["p2"]] == ["s3"]
    assert conn.counts() == {"wrapped_signal": 1}


@pytest.mark.asyncio
async def test_fetch_product_signals_empty_ids_no_query() -> None:
    conn = _RecordingConn(signal_evidence={}, fact_provenance={}, review_text={})
    assert await fetch_product_signals(_RecordingPool(conn), []) == {}
    assert conn.calls == []


# ---------------------------------------------------------------------------
# fetch_keyword_signal_triples (Phase 8 activation-hook keyword sidecar)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_keyword_signal_triples_selects_polarity_and_normalizes() -> None:
    class _TriplesConn(_RecordingConn):
        async def fetch(self, query: str, *args: Any) -> list[dict]:
            # keyword-scoped SELECT: polarity column + keyword_id NOT NULL filter.
            assert "FROM wrapped_signal" in query
            assert "polarity" in query
            assert "keyword_id IS NOT NULL" in query
            self.calls.append(("wrapped_signal", args))
            return [
                {"target_product_id": "p1", "bee_attr_id": "concept:BEEAttr:be",
                 "keyword_id": "concept:Keyword:kw_a", "polarity": "POS"},
                # polarity null -> "" (folds with a demo "" but never with "NEU").
                {"target_product_id": "p1", "bee_attr_id": None,
                 "keyword_id": "concept:Keyword:kw_b", "polarity": None},
                {"target_product_id": "p2", "bee_attr_id": "concept:BEEAttr:be",
                 "keyword_id": "concept:Keyword:kw_c", "polarity": "NEU"},
            ]

    conn = _TriplesConn(signal_evidence={}, fact_provenance={}, review_text={})
    result = await fetch_keyword_signal_triples(_RecordingPool(conn), ["p1", "p2"])

    assert result == {
        "p1": [
            ("concept:BEEAttr:be", "concept:Keyword:kw_a", "POS"),
            ("", "concept:Keyword:kw_b", ""),  # both nulls normalized to ""
        ],
        "p2": [("concept:BEEAttr:be", "concept:Keyword:kw_c", "NEU")],
    }
    assert conn.counts() == {"wrapped_signal": 1}  # one batched round-trip


@pytest.mark.asyncio
async def test_fetch_keyword_signal_triples_empty_ids_no_query() -> None:
    conn = _RecordingConn(signal_evidence={}, fact_provenance={}, review_text={})
    assert await fetch_keyword_signal_triples(_RecordingPool(conn), []) == {}
    assert conn.calls == []


# Guard: the module exposes exactly one asyncio-based entry so mistaken sync use
# fails fast in review rather than at runtime.
def test_prefetch_is_coroutine() -> None:
    provider = DBProvenanceProvider(pool=None)
    coro = provider.prefetch([])
    assert asyncio.iscoroutine(coro)
    coro.close()


# ---------------------------------------------------------------------------
# signal_ids_by_concept_path — semantic path IRI matching
#
# Semantic overlaps produce an ``axis:value:<IRI>`` concept_id (e.g.
# ``moisture:moist:concept:Keyword:kw_moist``) where the IRI is embedded, not
# leading. Prefix-only normalization kept the ``axis:value`` head and never
# matched a signal anchor, so every semantic path resolved to zero provenance.
# ---------------------------------------------------------------------------


def _path(concept_type: str, concept_id: str) -> ExplanationPath:
    return ExplanationPath(
        concept_type=concept_type,
        concept_id=concept_id,
        user_edge="PREFERS_KEYWORD",
        product_edge="HAS_BEE_KEYWORD_SIGNAL",
        contribution=1.0,
    )


def test_signal_ids_by_concept_path_matches_semantic_iri_paths() -> None:
    """A semantic path's embedded IRI is recovered and matched to the product's
    signal anchor — the real dense_golden shape (axis:value:concept:...)."""
    paths = [
        _path("semantic_keyword", "moisture:moist:concept:Keyword:kw_moist"),
        _path("semantic_bee_attr", "moisture:moist:concept:BEEAttr:bee_attr_moisturizing_power"),
    ]
    product_signals = [
        {"signal_id": "s_kw", "dst_id": "concept:Keyword:kw_moist",
         "keyword_id": "concept:Keyword:kw_moist", "bee_attr_id": None},
        {"signal_id": "s_ba", "dst_id": "concept:BEEAttr:bee_attr_moisturizing_power",
         "keyword_id": None, "bee_attr_id": "concept:BEEAttr:bee_attr_moisturizing_power"},
    ]
    assert signal_ids_by_concept_path(paths, product_signals) == {0: ["s_kw"], 1: ["s_ba"]}


def test_signal_ids_by_concept_path_bare_and_leading_iri_still_match() -> None:
    """Regression guard: bare-token (unit-test) and leading-IRI (real bare)
    concept ids keep matching exactly as before the embedded-IRI recovery."""
    paths = [
        _path("keyword", "kw_thin_spread"),        # bare token
        _path("keyword", "concept:Keyword:로션"),   # leading IRI (cut == 0, untouched)
    ]
    product_signals = [
        {"signal_id": "s_bare", "keyword_id": "kw_thin_spread"},
        {"signal_id": "s_iri", "keyword_id": "concept:Keyword:로션"},
    ]
    assert signal_ids_by_concept_path(paths, product_signals) == {0: ["s_bare"], 1: ["s_iri"]}


def test_signal_ids_by_concept_path_omits_semantic_path_without_signal() -> None:
    paths = [_path("semantic_keyword", "moisture:moist:concept:Keyword:kw_absent")]
    product_signals = [{"signal_id": "s_kw", "keyword_id": "concept:Keyword:kw_moist"}]
    assert signal_ids_by_concept_path(paths, product_signals) == {}


# ---------------------------------------------------------------------------
# Dense_golden real-data proof: every semantic explanation path resolves to a
# backing product signal (pre-fix this was zero).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _dense_semantic_paths() -> tuple[int, int]:
    """Run the dense_golden pipeline and, for every (user, product) with real
    semantic matches, count semantic explanation paths and how many resolve to
    >=1 signal via ``signal_ids_by_concept_path``. Returns (matched, total)."""
    import contextlib
    import io
    import json
    from pathlib import Path

    from src.common.enums import RecommendationMode
    from src.jobs.run_full_load import FullLoadConfig, run_full_load
    from src.rec.candidate_generator import generate_candidates
    from src.rec.explainer import explain
    from src.rec.product_profile_enrichment import enrich_product_profiles_by_master
    from src.rec.scorer import Scorer
    from src.rec.semantic_compatibility import find_semantic_matches

    dense = Path(__file__).resolve().parents[1] / "mockdata" / "dense_golden"
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_full_load(FullLoadConfig(
            review_json_path=str(dense / "review_triples_raw.json"),
            product_es_records=json.loads(
                (dense / "product_catalog_es.json").read_text(encoding="utf-8")
            ),
            user_profiles=json.loads(
                (dense / "user_profiles_normalized.json").read_text(encoding="utf-8")
            ),
            kg_mode="on",
        ))

    serving_products = enrich_product_profiles_by_master(
        result.serving_products, result.batch_result.get("product_masters", {}),
    )
    product_map = {str(p["product_id"]): p for p in serving_products}
    # Reconstruct product_signals exactly like src/web/state.py.
    product_signals: dict[str, list[dict]] = {}
    for review_result in result.batch_result.get("review_results", []):
        for sig in review_result.get("signals", []):
            pid = sig.get("target_product_id")
            if pid:
                product_signals.setdefault(str(pid), []).append(sig)

    scorer = Scorer()
    scorer.load_config()

    matched = 0
    total = 0
    for user in result.serving_users:
        for pid, product in product_map.items():
            if not find_semantic_matches(user, product):
                continue
            candidates = generate_candidates(
                user, [product], RecommendationMode.EXPLORE, max_candidates=50,
            )
            candidate = next((c for c in candidates if c.product_id == pid), None)
            if candidate is None:
                continue
            scored = scorer.score(user, product, candidate.overlap_concepts)
            exp = explain(scored, candidate.overlap_concepts, top_n=8)
            mapping = signal_ids_by_concept_path(exp.paths, product_signals.get(pid, []))
            for idx, path in enumerate(exp.paths):
                if path.concept_type.startswith(("semantic_", "weak_semantic_")):
                    total += 1
                    if mapping.get(idx):
                        matched += 1
    return matched, total


@pytest.mark.timeout(120)
def test_semantic_paths_resolve_to_signals_on_dense_golden(_dense_semantic_paths) -> None:
    matched, total = _dense_semantic_paths
    assert total > 0, "expected dense_golden to produce semantic explanation paths"
    # Every semantic path now resolves to >=1 backing signal (pre-fix: 0).
    assert matched == total, f"only {matched}/{total} semantic paths matched a signal"
