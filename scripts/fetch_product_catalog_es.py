#!/usr/bin/env python3
"""Product-master ES re-extraction backend (IC-3 / plan 2026-07-20 §5).

Confirmed source for the product master is Elasticsearch (ES9). This connector
adds the "input supply" the IC-2 refresh chain was waiting for: it exports the
FULL catalog from one or more ES indices and hands the records straight to the
existing :func:`scripts.refresh_product_catalog.refresh_product_catalog`
(contract validation → baseline diff → dated staging landing under the
git-ignored ``mockdata/real/products/`` tree). No re-implementation of the
validate/diff/land chain — this file only produces the records.

ES ACCESS (recommend-agent parity)
----------------------------------
Plain REST: ``POST {ES_CLOUD_URL}/{index}/_search`` with headers
``Authorization: ApiKey {ES_CLOUD_KEY}`` and ``Content-Type: application/json``.
Index env: ``ES_AMORE_INDEX`` (Amore, channel 031) + ``ES_INNI_INDEX``
(Innisfree, channel 036); the default export is the de-duplicated union of the
two. ES ``_source`` fields already match the catalog columns (``BRAND_NAME`` /
``CTGR_SS_NAME`` / ``ONLINE_PROD_SERIAL_NUMBER`` / ``REPRESENTATIVE_PROD_CODE``
/ …) — ``mockdata/product_catalog_es.json`` is exactly this export — so no field
remapping is needed.

PAGINATION (why search_after, not scroll/PIT)
---------------------------------------------
Full export uses ``search_after`` over a stable total order
(``sort=[{<unique id field>: asc}, {_doc: asc}]``; default key
``ONLINE_PROD_SERIAL_NUMBER`` = the diff SKU id, which is unique per catalog),
paging until a short/empty page. The last hit's ``sort`` array is echoed back
verbatim as the next ``search_after``, so the client is agnostic to how many
sort keys are configured.
  * vs scroll: no server-side scroll context to open/leak/clear, stateless, and
    ES9-forward (scroll is discouraged for deep pagination). Trade-off: no
    snapshot isolation — a catalog mutated mid-export could miss/duplicate a
    row. Acceptable because the product master is low-churn and the downstream
    contract + baseline-diff surface gross inconsistencies; switch to
    PIT+search_after if strict isolation is ever needed.
  * ``--sort-field`` overrides the sort key if the ES mapping makes the default
    non-sortable (e.g. point it at a ``.keyword`` subfield).

CREDENTIALS / SAFETY
--------------------
Live read-only export — NOT run in CI. Unit tests mock the HTTP layer only.
Credentials come from the environment (``.env`` via the opt-in loader, or the
shell); they are never hardcoded and never printed. Output is confined to the
git-ignored ``mockdata/real/products/`` tree by the shared staging guard.

Usage:
    python scripts/fetch_product_catalog_es.py
    python scripts/fetch_product_catalog_es.py --indices my-index --size 1000 --max-docs 5000
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Callable

# ── Paths ──
GRAPHRAPPING_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = GRAPHRAPPING_ROOT / "mockdata" / "product_catalog_es.json"

# Direct-run safety: put the repo root on sys.path so the lazy ``src.*`` /
# ``scripts.*`` imports resolve when invoked as
# ``python scripts/fetch_product_catalog_es.py`` (sys.path[0] would otherwise be
# the scripts/ dir). Harmless under pytest (repo root already on path).
if str(GRAPHRAPPING_ROOT) not in sys.path:
    sys.path.insert(0, str(GRAPHRAPPING_ROOT))

# Unique per-catalog sort key (the serving SKU id = the refresh diff key) — gives
# search_after a deterministic total order without a PIT/scroll context.
DEFAULT_SORT_FIELD = "ONLINE_PROD_SERIAL_NUMBER"
DEFAULT_BATCH_SIZE = 500

# (url, headers, json-body) -> parsed JSON object. Injected in tests to mock ES.
FetchFn = Callable[[str, Mapping[str, str], Mapping[str, Any]], dict[str, Any]]


class EsConfigError(RuntimeError):
    """Required ES environment configuration is missing/blank."""


class EsExportError(RuntimeError):
    """An ES request failed or returned an unusable response."""


# =============================================================================
# HTTP layer (stdlib only — httpx is not a base dependency)
# =============================================================================

def _default_fetch(
    url: str, headers: Mapping[str, str], body: Mapping[str, Any], *, timeout: float = 30.0
) -> dict[str, Any]:
    """POST a JSON body and return the parsed JSON object (stdlib urllib)."""
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # non-2xx — never echo request headers
        raise EsExportError(f"ES request failed: HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise EsExportError(f"ES request failed: {exc.reason}") from exc
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise EsExportError(f"unexpected ES response (not an object): {type(parsed).__name__}")
    return parsed


# =============================================================================
# Reader
# =============================================================================

class EsCatalogReader:
    """Full-catalog exporter over one or more ES indices via search_after paging.

    ``read()`` yields each document's ``_source`` dict across all configured
    indices (in order), capped at ``max_docs`` total when set. The HTTP layer is
    injectable (``fetch``) so tests never touch the network.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        indices: list[str],
        *,
        size: int = DEFAULT_BATCH_SIZE,
        max_docs: int | None = None,
        sort_field: str = DEFAULT_SORT_FIELD,
        fetch: FetchFn | None = None,
    ) -> None:
        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")
        if not indices:
            raise ValueError("at least one index is required")
        self._base_url = base_url.rstrip("/")
        self._indices = list(indices)
        self._size = size
        self._max_docs = max_docs
        self._sort_clause: list[dict[str, str]] = [{sort_field: "asc"}, {"_doc": "asc"}]
        self._fetch: FetchFn = fetch or _default_fetch
        self._headers = {
            "Authorization": f"ApiKey {api_key}",
            "Content-Type": "application/json",
        }

    @property
    def indices(self) -> list[str]:
        return list(self._indices)

    def _read_index(self, index: str) -> Iterator[dict[str, Any]]:
        endpoint = f"{self._base_url}/{index}/_search"
        search_after: list[Any] | None = None
        page = 0
        while True:
            body: dict[str, Any] = {
                "size": self._size,
                "query": {"match_all": {}},
                "sort": self._sort_clause,
                "track_total_hits": False,
            }
            if search_after is not None:
                body["search_after"] = search_after
            resp = self._fetch(endpoint, self._headers, body)
            page += 1
            hits_container = resp.get("hits")
            hits = hits_container.get("hits") if isinstance(hits_container, dict) else None
            if not hits:
                break
            for hit in hits:
                source = hit.get("_source") if isinstance(hit, Mapping) else None
                if isinstance(source, dict):
                    yield source
            if len(hits) < self._size:
                break  # last (partial) page — skip an extra empty round-trip
            last_sort = hits[-1].get("sort") if isinstance(hits[-1], Mapping) else None
            if not last_sort:
                raise EsExportError(
                    f"index {index!r}: hit on page {page} has no 'sort' value; cannot "
                    f"page with search_after (is {DEFAULT_SORT_FIELD!r} sortable? use "
                    "--sort-field to override)"
                )
            search_after = list(last_sort)

    def read(self) -> Iterator[dict[str, Any]]:
        """Yield ``_source`` dicts across all indices, capped at ``max_docs``."""
        count = 0
        for index in self._indices:
            for source in self._read_index(index):
                if self._max_docs is not None and count >= self._max_docs:
                    return
                yield source
                count += 1


def _missing_msg(missing: list[str], *, note: str | None = None) -> str:
    detail = f" ({note})" if note else ""
    return (
        f"missing required Elasticsearch env var(s): {missing}{detail}. Set them in "
        ".env (see .env.example) or export them in the shell before running."
    )


def reader_from_env(
    env: Mapping[str, str] | None = None,
    *,
    indices: list[str] | None = None,
    size: int = DEFAULT_BATCH_SIZE,
    max_docs: int | None = None,
    sort_field: str = DEFAULT_SORT_FIELD,
    fetch: FetchFn | None = None,
) -> EsCatalogReader:
    """Build an :class:`EsCatalogReader` from environment variables.

    Reads ``ES_CLOUD_URL`` / ``ES_CLOUD_KEY`` (both required) and, when
    ``indices`` is not given explicitly, the de-duplicated union of
    ``ES_AMORE_INDEX`` + ``ES_INNI_INDEX``. Raises :class:`EsConfigError` naming
    the missing variable(s).
    """
    source = env if env is not None else os.environ
    base_url = (source.get("ES_CLOUD_URL") or "").strip()
    api_key = (source.get("ES_CLOUD_KEY") or "").strip()
    missing = [name for name, val in (("ES_CLOUD_URL", base_url), ("ES_CLOUD_KEY", api_key)) if not val]
    if missing:
        raise EsConfigError(_missing_msg(missing))

    if indices is None:
        indices = []
        for env_key in ("ES_AMORE_INDEX", "ES_INNI_INDEX"):
            idx = (source.get(env_key) or "").strip()
            if idx and idx not in indices:
                indices.append(idx)
        if not indices:
            raise EsConfigError(
                _missing_msg(["ES_AMORE_INDEX", "ES_INNI_INDEX"], note="need at least one")
            )
    return EsCatalogReader(
        base_url, api_key, indices, size=size, max_docs=max_docs, sort_field=sort_field, fetch=fetch
    )


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    from src.common.env_file import load_env_file

    # Opt-in .env load (shell/CI values win — override=False). Import never does this.
    load_env_file(GRAPHRAPPING_ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indices", nargs="+", default=None,
                        help="ES index name(s) to export (default: ES_AMORE_INDEX + ES_INNI_INDEX)")
    parser.add_argument("--size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"per-request batch size (default {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--max-docs", type=int, default=None,
                        help="cap total exported documents (default: no cap)")
    parser.add_argument("--sort-field", default=DEFAULT_SORT_FIELD,
                        help=f"unique sort key for search_after paging (default {DEFAULT_SORT_FIELD})")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help=f"catalog to diff the export against (default: {DEFAULT_BASELINE})")
    parser.add_argument("--date", default=None, help="snapshot date YYYYMMDD (default: today)")
    parser.add_argument("--sample-n", type=int, default=10,
                        help="max SKU ids listed per change bucket (default 10)")
    args = parser.parse_args()

    from scripts.refresh_product_catalog import _load_records, refresh_product_catalog

    reader = reader_from_env(
        indices=args.indices, size=args.size, max_docs=args.max_docs, sort_field=args.sort_field
    )
    print("LIVE ES EXPORT (read-only _search) — not for CI.")
    print(f"  indices={reader.indices}  batch_size={args.size}  max_docs={args.max_docs}")

    records = list(reader.read())
    print(f"  exported {len(records)} document(s) across {len(reader.indices)} index(es)")

    date_str = args.date or _dt.datetime.now().strftime("%Y%m%d")
    baseline_records = _load_records(args.baseline)
    snapshot_path, manifest = refresh_product_catalog(
        records, baseline_records, date_str, sample_n=args.sample_n
    )

    print("=== PRODUCT CATALOG ES REFRESH (aggregates only) ===")
    print(json.dumps({
        "count": manifest["count"],
        "validation": manifest["validation"],
        "diff": manifest["diff"],
    }, ensure_ascii=False, indent=2))
    print(f"\nLanded {manifest['count']} products → {snapshot_path} (mode 0600)")


if __name__ == "__main__":
    main()
