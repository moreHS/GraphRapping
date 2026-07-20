"""IC-3 product-master ES re-extraction backend (plan 2026-07-20 §5).

Mock the HTTP layer only (never the network): search_after pagination merges
multi-page/multi-index results and terminates on a short page, the ApiKey header
+ endpoint are built per recommend-agent parity, ``max_docs`` caps the export,
env resolution names missing variables and de-dups the index union, and the
export output feeds the existing refresh chain unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.fetch_product_catalog_es import (
    DEFAULT_SORT_FIELD,
    EsCatalogReader,
    EsConfigError,
    EsExportError,
    reader_from_env,
)


def _prod(sku: str, **over: Any) -> dict[str, Any]:
    """A contract-valid product-catalog record (3-key identity + serving id)."""
    rec: dict[str, Any] = {
        "SOURCE_CHANNEL": "036", "SOURCE_KEY_TYPE": "chn_prd_cd",
        "SOURCE_PRODUCT_ID": f"P{sku}", "ONLINE_PROD_SERIAL_NUMBER": sku,
        "REPRESENTATIVE_PROD_CODE": "123456789", "BRAND_NAME": "brand",
        "ONLINE_PROD_NAME": "name",
    }
    rec.update(over)
    return rec


def _hits(sources: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "hits": {
            "hits": [
                {"_source": s, "sort": [s["ONLINE_PROD_SERIAL_NUMBER"]]} for s in sources
            ]
        }
    }


class _PagedEs:
    """Fake ES: pops the next canned page per index; records each request body."""

    def __init__(self, pages_by_index: dict[str, list[list[dict[str, Any]]]]) -> None:
        self._pages = {idx: list(pages) for idx, pages in pages_by_index.items()}
        self.requests: list[dict[str, Any]] = []

    def fetch(self, url: str, headers: Any, body: Any) -> dict[str, Any]:
        self.requests.append(
            {"url": url, "headers": dict(headers), "body": json.loads(json.dumps(body))}
        )
        index = url.rsplit("/", 2)[-2]
        queue = self._pages.get(index, [])
        return _hits(queue.pop(0) if queue else [])


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_merges_pages_and_terminates_on_short_page() -> None:
    fake = _PagedEs({"idx": [[_prod("1"), _prod("2")], [_prod("3")]]})  # full(2) then partial(1)
    reader = EsCatalogReader("http://es", "KEY", ["idx"], size=2, fetch=fake.fetch)
    records = list(reader.read())
    assert [r["ONLINE_PROD_SERIAL_NUMBER"] for r in records] == ["1", "2", "3"]
    assert len(fake.requests) == 2  # page1 full → fetch page2; page2 short → stop
    assert "search_after" not in fake.requests[0]["body"]
    assert fake.requests[1]["body"]["search_after"] == ["2"]  # echoed last sort


def test_empty_first_page_yields_nothing_single_request() -> None:
    fake = _PagedEs({"idx": [[]]})
    reader = EsCatalogReader("http://es", "KEY", ["idx"], size=5, fetch=fake.fetch)
    assert list(reader.read()) == []
    assert len(fake.requests) == 1


def test_reads_all_indices_in_order() -> None:
    fake = _PagedEs({"a": [[_prod("1")]], "b": [[_prod("2")]]})
    reader = EsCatalogReader("http://es", "K", ["a", "b"], size=5, fetch=fake.fetch)
    assert [r["ONLINE_PROD_SERIAL_NUMBER"] for r in list(reader.read())] == ["1", "2"]


def test_max_docs_caps_total_export() -> None:
    fake = _PagedEs({"idx": [[_prod("1"), _prod("2"), _prod("3")]]})
    reader = EsCatalogReader("http://es", "K", ["idx"], size=5, max_docs=2, fetch=fake.fetch)
    assert len(list(reader.read())) == 2


def test_full_page_without_sort_raises() -> None:
    def fetch(url: str, headers: Any, body: Any) -> dict[str, Any]:
        # full page (== size) but hits carry no 'sort' → cannot page with search_after
        return {"hits": {"hits": [{"_source": _prod("1")}, {"_source": _prod("2")}]}}

    reader = EsCatalogReader("http://es", "K", ["idx"], size=2, fetch=fetch)
    with pytest.raises(EsExportError, match="sort"):
        list(reader.read())


# ---------------------------------------------------------------------------
# Request shape (recommend-agent parity)
# ---------------------------------------------------------------------------

def test_apikey_header_endpoint_and_sort_clause() -> None:
    fake = _PagedEs({"idx": [[]]})
    reader = EsCatalogReader("http://es/", "SECRET", ["idx"], size=2, fetch=fake.fetch)
    list(reader.read())
    req = fake.requests[0]
    assert req["url"] == "http://es/idx/_search"  # trailing slash on base stripped
    assert req["headers"]["Authorization"] == "ApiKey SECRET"
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["body"]["sort"] == [{DEFAULT_SORT_FIELD: "asc"}, {"_doc": "asc"}]
    assert req["body"]["query"] == {"match_all": {}}


def test_custom_sort_field_used_in_body() -> None:
    fake = _PagedEs({"idx": [[]]})
    reader = EsCatalogReader("http://es", "K", ["idx"], size=2, sort_field="_id", fetch=fake.fetch)
    list(reader.read())
    assert fake.requests[0]["body"]["sort"] == [{"_id": "asc"}, {"_doc": "asc"}]


def test_reader_rejects_bad_construction() -> None:
    with pytest.raises(ValueError, match="positive"):
        EsCatalogReader("http://es", "K", ["idx"], size=0)
    with pytest.raises(ValueError, match="at least one index"):
        EsCatalogReader("http://es", "K", [])


# ---------------------------------------------------------------------------
# reader_from_env
# ---------------------------------------------------------------------------

def _noop_fetch(*_a: Any) -> dict[str, Any]:
    return {"hits": {"hits": []}}


def test_reader_from_env_missing_url_and_key() -> None:
    with pytest.raises(EsConfigError, match="ES_CLOUD_URL"):
        reader_from_env(env={})
    with pytest.raises(EsConfigError, match="ES_CLOUD_KEY"):
        reader_from_env(env={"ES_CLOUD_URL": "http://es"})


def test_reader_from_env_index_union_dedup() -> None:
    reader = reader_from_env(
        env={
            "ES_CLOUD_URL": "http://es", "ES_CLOUD_KEY": "K",
            "ES_AMORE_INDEX": "amore", "ES_INNI_INDEX": "amore",  # duplicate → deduped
        },
        fetch=_noop_fetch,
    )
    assert reader.indices == ["amore"]


def test_reader_from_env_index_union_both() -> None:
    reader = reader_from_env(
        env={
            "ES_CLOUD_URL": "http://es", "ES_CLOUD_KEY": "K",
            "ES_AMORE_INDEX": "amore", "ES_INNI_INDEX": "inni",
        },
        fetch=_noop_fetch,
    )
    assert reader.indices == ["amore", "inni"]


def test_reader_from_env_no_indices_raises() -> None:
    with pytest.raises(EsConfigError, match="ES_AMORE_INDEX"):
        reader_from_env(env={"ES_CLOUD_URL": "http://es", "ES_CLOUD_KEY": "K"})


def test_reader_from_env_explicit_indices_override() -> None:
    reader = reader_from_env(
        env={"ES_CLOUD_URL": "http://es", "ES_CLOUD_KEY": "K", "ES_AMORE_INDEX": "amore"},
        indices=["custom"],
        fetch=_noop_fetch,
    )
    assert reader.indices == ["custom"]


# ---------------------------------------------------------------------------
# Chain integrity: export → existing refresh_product_catalog
# ---------------------------------------------------------------------------

def test_export_feeds_refresh_chain(tmp_path: Path) -> None:
    from scripts.refresh_product_catalog import refresh_product_catalog

    fake = _PagedEs({"idx": [[_prod("1"), _prod("2")]]})
    reader = EsCatalogReader("http://es", "K", ["idx"], size=10, fetch=fake.fetch)
    records = list(reader.read())

    snap, manifest = refresh_product_catalog(records, [], "20260720", real_dir=tmp_path / "real")
    assert manifest["count"] == 2
    assert manifest["validation"]["violations"] == 0
    assert manifest["added"] == 2
    assert snap.exists()
