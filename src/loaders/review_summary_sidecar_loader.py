"""
Review-summary sidecar loader.

The review-summary ES aliases are product-scoped source summaries. They are
joined to GraphRapping products by clean source identity, then stored as a mart
sidecar. This module intentionally does not emit graph facts/signals.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.common.enums import (
    SOURCE_KEY_COLLISION_MARKER_PREFIX,
    SOURCE_KEY_COLLISION_QUALITY,
)


OWN_CHANNEL_TO_CATEGORY = {
    "031": "own-apmall",
    "036": "own-innisfree",
    "039": "own-osulloc",
    "048": "own-aritaum",
}

MATCH_RANK = {
    "exact_category": 2,
    "product_id_ambiguous_skipped": 1,
    "not_found": 0,
}

COLLISION_QUALITY = SOURCE_KEY_COLLISION_QUALITY
COLLISION_PREFIX = SOURCE_KEY_COLLISION_MARKER_PREFIX


@dataclass(frozen=True)
class LookupBuildResult:
    products: list[dict[str, Any]]
    product_count: int
    collision_excluded: int
    missing_source_identity_excluded: int


@dataclass(frozen=True)
class MatchResult:
    status: str
    doc: dict[str, Any] | None
    candidate_count: int
    category_candidate_count: int
    candidate_doc_ids: list[str]
    candidate_categories: list[str]
    reason: str

    def metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidate_count": self.candidate_count,
            "category_candidate_count": self.category_candidate_count,
            "candidate_doc_ids": self.candidate_doc_ids,
            "candidate_categories": self.candidate_categories,
            "reason": self.reason,
        }


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse a small dotenv file without mutating os.environ."""
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            env[key] = value
    return env


def read_docs_file(path: str | Path) -> list[dict[str, Any]]:
    """Read ES hits from JSON or JSONL.

    Accepted JSON shapes:
    - `[hit, ...]`
    - `{"hits": {"hits": [hit, ...]}}`
    - `{"documents": [hit, ...]}`
    """
    p = Path(path)
    if p.suffix.lower() == ".jsonl":
        docs: list[dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                docs.append(json.loads(stripped))
        return docs

    payload = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        hits = payload.get("hits")
        if isinstance(hits, dict) and isinstance(hits.get("hits"), list):
            return list(hits["hits"])
        documents = payload.get("documents")
        if isinstance(documents, list):
            return documents
    raise ValueError(f"Unsupported review-summary docs file shape: {p}")


def fetch_es_alias_docs(
    es_url: str,
    api_key: str,
    alias: str,
    *,
    page_size: int = 500,
    scroll: str = "2m",
    max_docs: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch all docs from an ES alias using match_all scroll.

    This function deliberately sends no local GraphRapping product ids to ES.
    Filtering and joining happen locally after the alias-wide export.
    """
    base = es_url.rstrip("/")
    docs: list[dict[str, Any]] = []
    scroll_id: str | None = None
    try:
        first_url = f"{base}/{urllib.parse.quote(alias)}/_search?scroll={urllib.parse.quote(scroll)}"
        first_body = {
            "size": page_size,
            "query": {"match_all": {}},
            "sort": ["_doc"],
            "_source": True,
        }
        payload = _es_post(first_url, api_key, first_body)
        scroll_id = payload.get("_scroll_id")
        hits = _hits(payload)
        docs.extend(_cap_docs(hits, max_docs=max_docs, current_count=len(docs)))

        while hits and (max_docs is None or len(docs) < max_docs):
            if not scroll_id:
                break
            payload = _es_post(
                f"{base}/_search/scroll",
                api_key,
                {"scroll": scroll, "scroll_id": scroll_id},
            )
            scroll_id = payload.get("_scroll_id") or scroll_id
            hits = _hits(payload)
            docs.extend(_cap_docs(hits, max_docs=max_docs, current_count=len(docs)))
    finally:
        if scroll_id:
            _clear_scroll(base, api_key, scroll_id)
    return docs


def build_lookup_products(rows: list[dict[str, Any]]) -> LookupBuildResult:
    """Build clean product lookup rows from DB product/serving rows."""
    products: list[dict[str, Any]] = []
    collision_excluded = 0
    missing_source_identity_excluded = 0
    for row in rows:
        source_product_id = _text(row.get("source_product_id"))
        source_truth_quality = _text(row.get("source_truth_quality"))
        if source_truth_quality == COLLISION_QUALITY or _is_collision_marker(source_product_id):
            collision_excluded += 1
            continue
        if not source_product_id:
            missing_source_identity_excluded += 1
            continue

        source_channel = _text(row.get("source_channel"))
        review_summary_category = derive_review_summary_category("own", source_channel)
        review_source = "own" if review_summary_category else None
        products.append({
            "product_id": _text(row.get("product_id")),
            "source_product_id": source_product_id,
            "source_channel": source_channel,
            "source_key_type": _text(row.get("source_key_type")),
            "product_name": row.get("product_name"),
            "brand_name": row.get("brand_name"),
            "source_truth_quality": source_truth_quality,
            "review_source": review_source,
            "review_channel": source_channel,
            "review_summary_category": review_summary_category,
        })
    return LookupBuildResult(
        products=products,
        product_count=len(rows),
        collision_excluded=collision_excluded,
        missing_source_identity_excluded=missing_source_identity_excluded,
    )


def derive_review_summary_category(review_source: str | None, channel: str | None) -> str | None:
    source = _text(review_source)
    if source != "own":
        return None
    return OWN_CHANNEL_TO_CATEGORY.get(_text(channel) or "")


def group_docs_by_product_id(docs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        product_id = doc_product_id(doc)
        if product_id:
            grouped[product_id].append(doc)
    return dict(grouped)


def choose_review_summary_match(
    product: dict[str, Any],
    docs_by_product_id: dict[str, list[dict[str, Any]]],
) -> MatchResult:
    source_product_id = _text(product.get("source_product_id"))
    candidates = docs_by_product_id.get(source_product_id or "", [])
    categories = _unique([doc_category(doc) for doc in candidates])
    candidate_ids = [did for doc in candidates if (did := doc_id(doc))]
    if not candidates:
        return MatchResult(
            status="not_found",
            doc=None,
            candidate_count=0,
            category_candidate_count=0,
            candidate_doc_ids=[],
            candidate_categories=[],
            reason="no_es_doc_for_source_product_id",
        )

    expected_category = _text(product.get("review_summary_category"))
    if expected_category:
        category_candidates = [doc for doc in candidates if doc_category(doc) == expected_category]
        if category_candidates:
            return MatchResult(
                status="exact_category",
                doc=_best_doc(category_candidates),
                candidate_count=len(candidates),
                category_candidate_count=len(category_candidates),
                candidate_doc_ids=candidate_ids,
                candidate_categories=categories,
                reason="source_channel_category_matched",
            )
        return MatchResult(
            status="product_id_ambiguous_skipped",
            doc=None,
            candidate_count=len(candidates),
            category_candidate_count=0,
            candidate_doc_ids=candidate_ids,
            candidate_categories=categories,
            reason="source_product_id_found_but_expected_category_missing",
        )

    return MatchResult(
        status="product_id_ambiguous_skipped",
        doc=None,
        candidate_count=len(candidates),
        category_candidate_count=0,
        candidate_doc_ids=candidate_ids,
        candidate_categories=categories,
        reason="missing_or_unmatched_review_summary_category",
    )


def build_sidecar_rows(
    products: list[dict[str, Any]],
    long_docs: list[dict[str, Any]],
    short_docs: list[dict[str, Any]],
    *,
    product_count: int | None = None,
    collision_excluded: int = 0,
    missing_source_identity_excluded: int = 0,
    source: str = "es8_summary_review",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    long_by_product = group_docs_by_product_id(long_docs)
    short_by_product = group_docs_by_product_id(short_docs)
    rows: list[dict[str, Any]] = []
    counts = {
        "matched": 0,
        "exact_category": 0,
        "source_unique": 0,
        "product_id_unique": 0,
        "ambiguous_skipped": 0,
        "not_found": 0,
    }

    for product in products:
        long_match = choose_review_summary_match(product, long_by_product)
        short_match = choose_review_summary_match(product, short_by_product)
        match_status = _overall_match_status(long_match, short_match)
        if match_status == "exact_category":
            counts["matched"] += 1
            counts[match_status] += 1
        elif match_status == "product_id_ambiguous_skipped":
            counts["ambiguous_skipped"] += 1
        else:
            counts["not_found"] += 1

        long_doc = long_match.doc
        short_doc = short_match.doc
        rows.append({
            "product_id": product["product_id"],
            "source_product_id": product["source_product_id"],
            "source_channel": product.get("source_channel"),
            "source_key_type": product.get("source_key_type"),
            "review_source": product.get("review_source"),
            "review_channel": product.get("review_channel"),
            "review_summary_category": product.get("review_summary_category"),
            "match_status": match_status,
            "long_doc_id": doc_id(long_doc) if long_doc else None,
            "short_doc_id": doc_id(short_doc) if short_doc else None,
            "long_doc": long_doc,
            "short_doc": short_doc,
            "candidate_metadata": {
                "long": long_match.metadata(),
                "short": short_match.metadata(),
                "source_identity": _source_identity(product),
            },
            "normalized_summary": _normalized_summary(product, long_doc, short_doc, match_status),
            "an_date": _max_text([doc_an_date(long_doc), doc_an_date(short_doc)]),
            "source": source,
        })

    manifest = {
        "source": source,
        "product_count": product_count if product_count is not None else len(products),
        "clean_lookup_product_count": len(products),
        "fetched_long_docs": len(long_docs),
        "fetched_short_docs": len(short_docs),
        "collision_excluded": collision_excluded,
        "missing_source_identity_excluded": missing_source_identity_excluded,
        **counts,
        "payload": {
            "match_status_counts": counts,
            "collision_excluded": collision_excluded,
            "missing_source_identity_excluded": missing_source_identity_excluded,
        },
    }
    return rows, manifest


def doc_id(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    return _text(doc.get("_id")) or _text(_doc_source(doc).get("_id"))


def doc_product_id(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    src = _doc_source(doc)
    return _text(src.get("product_id") or src.get("prd_id") or src.get("ecp_onln_prd_srno"))


def doc_category(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    return _text(_doc_source(doc).get("category"))


def doc_source_family(doc: dict[str, Any] | None) -> str | None:
    category = doc_category(doc)
    if category and "-" in category:
        return category.split("-", 1)[0]
    if doc:
        return _text(_doc_source(doc).get("source"))
    return None


def doc_review_count(doc: dict[str, Any] | None) -> int:
    if not doc:
        return 0
    value = _doc_source(doc).get("review_cnt")
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def doc_timestamp(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    return _text(_doc_source(doc).get("@timestamp"))


def doc_an_date(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    return _text(_doc_source(doc).get("An_date"))


def _doc_source(doc: dict[str, Any]) -> dict[str, Any]:
    source = doc.get("_source")
    if isinstance(source, dict):
        return source
    return doc


def _best_doc(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return max(docs, key=lambda doc: (doc_timestamp(doc) or "", doc_review_count(doc), doc_id(doc) or ""))


def _overall_match_status(long_match: MatchResult, short_match: MatchResult) -> str:
    statuses = [long_match.status, short_match.status]
    return max(statuses, key=lambda status: MATCH_RANK[status])


def _normalized_summary(
    product: dict[str, Any],
    long_doc: dict[str, Any] | None,
    short_doc: dict[str, Any] | None,
    match_status: str,
) -> dict[str, Any] | None:
    if not long_doc and not short_doc:
        return None
    return {
        "match_status": match_status,
        "source_identity": _source_identity(product),
        "long": _summary_projection(long_doc),
        "short": _summary_projection(short_doc),
    }


def _summary_projection(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    src = _doc_source(doc)
    keys = [
        "summary",
        "review_cnt",
        "sku_count",
        "sku_breakdown",
        "An_date",
        "@timestamp",
        "category",
        "source",
        "channel",
        "product_id",
        "prd_nm",
        "ecp_onln_prd_nm",
        "rprs_prd_nm",
        "brand_name",
        "brnd_cd",
        "sktp_nm",
        "sktr_nm",
    ]
    return {key: src[key] for key in keys if key in src}


def _source_identity(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": product.get("product_id"),
        "source_product_id": product.get("source_product_id"),
        "source_channel": product.get("source_channel"),
        "source_key_type": product.get("source_key_type"),
        "review_summary_category": product.get("review_summary_category"),
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_collision_marker(value: str | None) -> bool:
    return bool(value and value.startswith(COLLISION_PREFIX))


def _unique(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _max_text(values: list[str | None]) -> str | None:
    present = [value for value in values if value]
    return max(present) if present else None


def _hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    hits = payload.get("hits")
    if not isinstance(hits, dict):
        return []
    raw_hits = hits.get("hits")
    return list(raw_hits) if isinstance(raw_hits, list) else []


def _cap_docs(
    hits: list[dict[str, Any]],
    *,
    max_docs: int | None,
    current_count: int,
) -> list[dict[str, Any]]:
    if max_docs is None:
        return hits
    remaining = max_docs - current_count
    if remaining <= 0:
        return []
    return hits[:remaining]


def _es_post(url: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
            return payload
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ES request failed: status={exc.code} body={body_text[:300]}") from exc


def _clear_scroll(base_url: str, api_key: str, scroll_id: str) -> None:
    try:
        _es_request(
            f"{base_url}/_search/scroll",
            api_key,
            {"scroll_id": [scroll_id]},
            method="DELETE",
        )
    except Exception:
        return


def _es_request(url: str, api_key: str, body: dict[str, Any], *, method: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return payload


def es_config_from_env(env_file: str | Path | None = None) -> tuple[str, str]:
    env = dict(os.environ)
    if env_file:
        env.update(load_env_file(env_file))
    es_url = env.get("ES_CLOUD_URL") or env.get("ELASTICSEARCH_URL")
    api_key = env.get("ES_CLOUD_KEY") or env.get("ELASTICSEARCH_API_KEY")
    if not es_url or not api_key:
        raise RuntimeError("Missing ES_CLOUD_URL/ES_CLOUD_KEY for review-summary export.")
    return es_url, api_key
