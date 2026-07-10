"""
In-memory provenance provider for the demo pipeline.

Mirrors the async `ProvenanceProvider` Protocol used by `ExplanationService`
(see `src/rec/explainer.py`), so it is a drop-in alternative to the
DB-backed `DBProvenanceProvider` (`src/db/repos/provenance_repo.py`).

Data source: pipeline-run artifacts (`batch_result["all_bundles"]`), which
carry the full provenance chain in memory:

    wrapped signal ──(signal_evidence_rows)──▶ canonical_fact
        └──(fact.provenance / FactProvenance)──▶ raw row + review_id
        └── review_raw["review_text"] ─────────▶ original review text

Provenance source of truth is `signal_evidence` (not `WrappedSignal.source_fact_ids`),
consistent with the DB path.

Concept→signal mapping (provenance 정합성): an explanation path names a concept
(e.g. `keyword:kw_thin_spread`), NOT a signal. To attach only the reviews that
actually evidence that concept for the recommended product, `signal_ids_by_concept_path`
resolves each path to the product's signals whose `dst_id` / `keyword_id` /
`bee_attr_id` normalize to the path's `concept_id`. Feeding this mapping to
`ExplanationService.explain_with_provenance(signal_ids_by_concept=...)` prevents
an unrelated review from being attached to a path.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.rec.explainer import ExplanationPath
from src.rec.semantic_compatibility import normalize_signal_id


class InMemoryProvenanceProvider:
    """`ProvenanceProvider` implementation backed by in-memory pipeline artifacts.

    Satisfies the same async Protocol as `DBProvenanceProvider` so that
    `ExplanationService(provider)` works identically for the demo and for DB.
    Async methods keep the interface ready for the DB implementation; the
    in-memory lookups themselves are synchronous dict reads.
    """

    def __init__(
        self,
        *,
        signal_evidence_by_signal: dict[str, list[dict]],
        fact_provenance_by_fact: dict[str, list[dict]],
        review_text_by_id: dict[str, str],
    ) -> None:
        self._signal_evidence_by_signal = signal_evidence_by_signal
        self._fact_provenance_by_fact = fact_provenance_by_fact
        self._review_text_by_id = review_text_by_id

    # --- ProvenanceProvider Protocol (async, mirrors DBProvenanceProvider) ---

    async def get_signal_evidence(self, signal_id: str) -> list[dict]:
        return list(self._signal_evidence_by_signal.get(signal_id, []))

    async def get_fact_provenance(self, fact_id: str) -> list[dict]:
        return list(self._fact_provenance_by_fact.get(fact_id, []))

    async def get_review_snippet(
        self,
        review_id: str,
        start: int | None,
        end: int | None,
    ) -> str | None:
        text = self._review_text_by_id.get(review_id)
        if text is None:
            return None
        if start is not None and end is not None:
            return text[start:end]
        return text

    # --- Introspection (demo/debug only) ---

    @property
    def review_count(self) -> int:
        return len(self._review_text_by_id)

    @property
    def signal_count(self) -> int:
        return len(self._signal_evidence_by_signal)


def build_inmemory_provenance_provider(batch_result: dict[str, Any]) -> InMemoryProvenanceProvider:
    """Build an `InMemoryProvenanceProvider` from a `run_batch` result.

    Reads `batch_result["all_bundles"]` (list of `ReviewPersistBundle`). Each
    bundle supplies:
      - `signal_evidence_rows`  → signal_id → [{fact_id, evidence_rank, ...}]
      - `canonical_facts[*].provenance` → fact_id → [FactProvenance-as-dict]
      - `review_raw["review_text"]`     → review_id → text

    Falls back to empty indexes when bundles are absent (e.g. DB-mode batch
    results that don't carry in-memory bundles), yielding a provider that
    simply produces no snippets rather than raising.
    """
    signal_evidence_by_signal: dict[str, list[dict]] = {}
    fact_provenance_by_fact: dict[str, list[dict]] = {}
    review_text_by_id: dict[str, str] = {}

    for bundle in batch_result.get("all_bundles", []) or []:
        review_raw = getattr(bundle, "review_raw", None) or {}
        review_id = getattr(bundle, "review_id", "") or review_raw.get("review_id", "")
        text = review_raw.get("review_text")
        if review_id and isinstance(text, str) and text:
            review_text_by_id.setdefault(review_id, text)

        for ev in getattr(bundle, "signal_evidence_rows", []) or []:
            sid = ev.get("signal_id")
            if sid:
                signal_evidence_by_signal.setdefault(sid, []).append(dict(ev))

        for fact in getattr(bundle, "canonical_facts", []) or []:
            fact_id = getattr(fact, "fact_id", "")
            if not fact_id or fact_id in fact_provenance_by_fact:
                continue
            prov_rows: list[dict] = []
            for prov in getattr(fact, "provenance", []) or []:
                prov_rows.append({
                    "fact_id": fact_id,
                    "raw_table": getattr(prov, "raw_table", ""),
                    "raw_row_id": getattr(prov, "raw_row_id", ""),
                    "review_id": getattr(prov, "review_id", "") or fact.review_id,
                    "snippet": getattr(prov, "snippet", "") or "",
                    "start_offset": getattr(prov, "start_offset", None),
                    "end_offset": getattr(prov, "end_offset", None),
                    "source_modality": getattr(prov, "source_modality", ""),
                    "evidence_rank": getattr(prov, "evidence_rank", 0),
                    "source_domain": getattr(prov, "source_domain", "review"),
                    "source_kind": getattr(prov, "source_kind", "raw"),
                })
            # A canonical fact with no explicit provenance still resolves to its
            # own review (review_text fallback covers the empty-snippet case).
            if not prov_rows and getattr(fact, "review_id", None):
                prov_rows.append({
                    "fact_id": fact_id,
                    "raw_table": "",
                    "raw_row_id": "",
                    "review_id": fact.review_id,
                    "snippet": "",
                    "start_offset": None,
                    "end_offset": None,
                    "source_modality": "",
                    "evidence_rank": 0,
                    "source_domain": "review",
                    "source_kind": "raw",
                })
            if prov_rows:
                fact_provenance_by_fact[fact_id] = prov_rows

    return InMemoryProvenanceProvider(
        signal_evidence_by_signal=signal_evidence_by_signal,
        fact_provenance_by_fact=fact_provenance_by_fact,
        review_text_by_id=review_text_by_id,
    )


# Signal fields whose value is a concept/entity IRI that an explanation path's
# concept_id can match against. dst_id is the primary anchor; keyword_id /
# bee_attr_id disambiguate BEE keyword vs attribute paths.
_SIGNAL_CONCEPT_FIELDS = ("dst_id", "keyword_id", "bee_attr_id")


def _concept_path_match_key(concept_id: str) -> str:
    """Recover the signal-anchor comparison key from an explanation path's concept_id.

    Most paths carry a bare concept id — either a raw token (`kw_thin_spread`,
    unit tests) or a leading IRI (`concept:Keyword:로션`, real data) — which
    ``normalize_signal_id`` collapses to the same key as a signal's anchor.

    Semantic paths (`semantic_*` / `weak_semantic_*`) are different: their
    concept_id is ``axis:value:<IRI>`` (e.g. ``moisture:moist:concept:Keyword:
    kw_moist``), so the IRI is *embedded*, not leading. ``normalize_signal_id``
    only strips a leading ``concept:`` / ``product:`` prefix, so it would keep
    the ``axis:value`` head and never match a signal anchor (``kw_moist``).
    When ``concept:`` or ``product:`` appears embedded (not at position 0),
    recover the trailing IRI from its last occurrence before normalizing; a bare
    leading IRI (cut == 0) or a prefix-less token is left for
    ``normalize_signal_id`` to handle exactly as before (no regression).
    """
    cid = str(concept_id or "")
    cut = max(cid.rfind("concept:"), cid.rfind("product:"))
    if cut > 0:
        cid = cid[cut:]
    return normalize_signal_id(cid)


def signal_ids_by_concept_path(
    paths: list[ExplanationPath],
    product_signals: list[dict],
) -> dict[int, list[str]]:
    """Map each explanation path (by index) to the product's matching signal_ids.

    A signal matches a path when any of the signal's concept anchors
    (`dst_id` / `keyword_id` / `bee_attr_id`) normalizes to the same key as the
    path's recovered concept key (see `_concept_path_match_key`, which handles
    both bare-id paths and the `axis:value:<IRI>` form used by semantic paths).
    `product_signals` MUST be the signal list for the recommended product only
    (e.g. `demo_state.product_signals[product_id]`) so that no other product's
    reviews leak in.

    Returns `{path_index: [signal_id, ...]}`. Paths whose concept has no backing
    review signal (e.g. brand/category/purchase-behavior paths) are omitted.
    """
    # Build concept-key → signal_ids index for this product once.
    signal_ids_by_key: dict[str, list[str]] = {}
    for sig in product_signals:
        sid = sig.get("signal_id")
        if not sid:
            continue
        keys = {
            normalize_signal_id(sig.get(field))
            for field in _SIGNAL_CONCEPT_FIELDS
            if sig.get(field)
        }
        for key in keys:
            if not key:
                continue
            bucket = signal_ids_by_key.setdefault(key, [])
            if sid not in bucket:
                bucket.append(sid)

    result: dict[int, list[str]] = {}
    for idx, path in enumerate(paths):
        key = _concept_path_match_key(path.concept_id)
        if not key:
            continue
        matches = signal_ids_by_key.get(key)
        if matches:
            result[idx] = list(matches)
    return result


# ---------------------------------------------------------------------------
# DB-backed provenance provider (Phase 2.1 / 0.4): request-batched, no N+1.
# ---------------------------------------------------------------------------

# fact_provenance columns needed to build a SnippetEvidence (mirror of
# src/db/repos/provenance_repo.get_fact_provenance). review_id is nullable.
_FACT_PROVENANCE_COLUMNS = (
    "fact_id, raw_table, raw_row_id, review_id, snippet, start_offset, "
    "end_offset, source_modality, evidence_rank, source_domain, source_kind"
)


class DBProvenanceProvider:
    """Request-batched `ProvenanceProvider` backed by an asyncpg pool.

    Satisfies the same async Protocol as `InMemoryProvenanceProvider` (see
    `src/rec/explainer.py::ProvenanceProvider`) but, unlike the per-call
    `src/db/repos/provenance_repo.DBProvenanceProvider` (one query per
    signal/fact — an N+1 storm under a recommendation request), it pulls the
    whole ``signal_evidence → canonical_fact → fact_provenance → review_raw``
    chain for a *batch* of signal_ids in a fixed number of round-trips via
    :meth:`prefetch`, then serves the Protocol reads from in-memory caches.

    Typical use (once per ``/api/recommend`` request)::

        provider = DBProvenanceProvider(pool)
        await provider.prefetch(all_signal_ids_across_all_paths)
        # ExplanationService(provider).explain_with_provenance(...) now does
        # only O(1) cache reads.

    ``review_id`` is nullable in ``fact_provenance``; rows are returned with
    ``review_id=None`` intact so ``ExplanationService`` pairs each snippet with
    its own id (or None) rather than mis-aligning parallel lists.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._signal_evidence_by_signal: dict[str, list[dict]] = {}
        self._fact_provenance_by_fact: dict[str, list[dict]] = {}
        self._review_text_by_id: dict[str, str | None] = {}
        self._prefetched_signals: set[str] = set()
        self._prefetched_facts: set[str] = set()
        self._prefetched_reviews: set[str] = set()

    async def prefetch(self, signal_ids: Iterable[str]) -> None:
        """Batch-load the provenance chain for ``signal_ids`` in ≤3 queries.

        Idempotent and incremental: already-prefetched signals/facts/reviews are
        skipped, so calling it again (or with overlapping ids) issues no
        redundant work. Signals with no evidence are still recorded so later
        reads return ``[]`` without a DB hit.
        """
        new_signals = [
            sid
            for sid in dict.fromkeys(signal_ids)
            if sid and sid not in self._prefetched_signals
        ]
        if not new_signals:
            return

        async with self._pool.acquire() as conn:
            # Query 1: signal_evidence for the whole signal batch.
            ev_rows = await conn.fetch(
                "SELECT signal_id, fact_id, evidence_rank, contribution "
                "FROM signal_evidence WHERE signal_id = ANY($1) ORDER BY evidence_rank",
                new_signals,
            )
            for sid in new_signals:
                self._signal_evidence_by_signal.setdefault(sid, [])
            new_fact_ids: list[str] = []
            for record in ev_rows:
                row = dict(record)
                self._signal_evidence_by_signal.setdefault(row["signal_id"], []).append(row)
                fid = row.get("fact_id")
                if fid and fid not in self._prefetched_facts:
                    new_fact_ids.append(fid)
            self._prefetched_signals.update(new_signals)

            fact_ids = list(dict.fromkeys(new_fact_ids))
            new_review_ids: list[str] = []
            if fact_ids:
                # Query 2: fact_provenance for every fact referenced above.
                prov_rows = await conn.fetch(
                    f"SELECT {_FACT_PROVENANCE_COLUMNS} "
                    "FROM fact_provenance WHERE fact_id = ANY($1) ORDER BY evidence_rank",
                    fact_ids,
                )
                for fid in fact_ids:
                    self._fact_provenance_by_fact.setdefault(fid, [])
                for record in prov_rows:
                    row = dict(record)
                    self._fact_provenance_by_fact.setdefault(row["fact_id"], []).append(row)
                    review_id = row.get("review_id")
                    # Only rows lacking a stored snippet need the raw review text
                    # (mirrors ExplanationService's fallback condition).
                    if (
                        review_id
                        and not row.get("snippet")
                        and review_id not in self._prefetched_reviews
                    ):
                        new_review_ids.append(review_id)
                self._prefetched_facts.update(fact_ids)

            review_ids = list(dict.fromkeys(new_review_ids))
            if review_ids:
                # Query 3: raw review text for snippet fallback.
                text_rows = await conn.fetch(
                    "SELECT review_id, review_text FROM review_raw WHERE review_id = ANY($1)",
                    review_ids,
                )
                for record in text_rows:
                    row = dict(record)
                    self._review_text_by_id[row["review_id"]] = row.get("review_text")
                self._prefetched_reviews.update(review_ids)

    # --- ProvenanceProvider Protocol (async; cache reads after prefetch) ---

    async def get_signal_evidence(self, signal_id: str) -> list[dict]:
        return [dict(row) for row in self._signal_evidence_by_signal.get(signal_id, [])]

    async def get_fact_provenance(self, fact_id: str) -> list[dict]:
        return [dict(row) for row in self._fact_provenance_by_fact.get(fact_id, [])]

    async def get_review_snippet(
        self,
        review_id: str,
        start: int | None,
        end: int | None,
    ) -> str | None:
        text = self._review_text_by_id.get(review_id)
        if text is None:
            return None
        if start is not None and end is not None:
            return text[start:end]
        return text


async def fetch_product_signals(
    pool: Any,
    product_ids: Iterable[str],
) -> dict[str, list[dict]]:
    """Batch-fetch per-product raw signals for provenance path resolution.

    Returns ``{product_id: [{signal_id, dst_id, keyword_id, bee_attr_id,
    target_product_id}, ...]}`` in a single query — the DB-mode equivalent of
    ``demo_state.product_signals`` for feeding :func:`signal_ids_by_concept_path`.
    """
    ids = [pid for pid in dict.fromkeys(product_ids) if pid]
    if not ids:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT signal_id, target_product_id, dst_id, keyword_id, bee_attr_id "
            "FROM wrapped_signal WHERE target_product_id = ANY($1)",
            ids,
        )
    result: dict[str, list[dict]] = {}
    for record in rows:
        row = dict(record)
        pid = row.get("target_product_id")
        if pid:
            result.setdefault(pid, []).append(row)
    return result
