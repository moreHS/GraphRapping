"""IC-R review-triple connector (plan §4·§6).

Reader interface + file backend (both formats, .json array / .jsonl lines /
directory), contract-driven rejection + threshold abort, cumulative snapshot
landing (added/updated/unchanged/carried_forward, conflict hard-fail vs.
--allow-updates, format-mixing refusal), determinism/atomicity, and e2e
consumption of a landed snapshot by the demo pipeline (rs_jsonl) and the
in-memory full-load (relation).
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.fetch_review_triples import (
    FileReader,
    ReviewLandingError,
    ReviewTripleReader,
    land_review_triples,
)

MOCK = Path("mockdata")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _rel(key: str, text: str = "좋아요") -> dict[str, Any]:
    return {
        "source_review_key": key, "drup_dt": "2026-01-01", "channel": "031",
        "text": text, "source_product_id": "100",
        "ner": [], "bee": [], "relation": [],
    }


def _rs(rid: str, text: str = "좋아요") -> dict[str, Any]:
    return {
        "id": rid, "text": text, "date": "2026-01-01", "channel": "031",
        "product_id": "100", "ner_spans": [], "bee_spans": [],
    }


class _ListReader(ReviewTripleReader):
    """In-memory backend — proves the interface is implementable off-file."""

    def __init__(self, records: list[dict[str, Any]], fmt: str) -> None:
        self._records = records
        self._fmt = fmt

    @property
    def format(self) -> str:
        return self._fmt

    def read(self) -> Iterator[dict[str, Any]]:
        return iter(self._records)


# ---------------------------------------------------------------------------
# FileReader backend
# ---------------------------------------------------------------------------

def test_file_reader_json_array_relation(tmp_path: Path) -> None:
    src = tmp_path / "reviews.json"
    src.write_text(json.dumps([_rel("K1"), _rel("K2")]), encoding="utf-8")
    reader = FileReader(src, "relation")
    assert reader.format == "relation"
    keys = [r["source_review_key"] for r in reader.read()]
    assert keys == ["K1", "K2"]


def test_file_reader_jsonl_lines_rs(tmp_path: Path) -> None:
    src = tmp_path / "reviews.jsonl"
    src.write_text("\n".join(json.dumps(_rs(f"R{i}")) for i in range(3)) + "\n", encoding="utf-8")
    ids = [r["id"] for r in FileReader(src, "rs_jsonl").read()]
    assert ids == ["R0", "R1", "R2"]


def test_file_reader_directory_merges_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text(json.dumps([_rel("K2")]), encoding="utf-8")
    (tmp_path / "a.jsonl").write_text(json.dumps(_rel("K1")) + "\n", encoding="utf-8")
    keys = [r["source_review_key"] for r in FileReader(tmp_path, "relation").read()]
    assert keys == ["K1", "K2"]  # a.jsonl before b.json (sorted)


def test_file_reader_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown format"):
        FileReader(tmp_path / "x.json", "parquet")


def test_file_reader_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ReviewLandingError, match="not found"):
        list(FileReader(tmp_path / "nope.json", "relation").read())


# ---------------------------------------------------------------------------
# Contract-driven rejection + threshold
# ---------------------------------------------------------------------------

def test_invalid_records_dropped_under_threshold(tmp_path: Path) -> None:
    records = [_rel(f"K{i}") for i in range(19)] + [{"source_review_key": "BAD"}]  # 1/20 = 5%
    _snap, manifest = land_review_triples(
        _ListReader(records, "relation"), "20260720", real_dir=tmp_path / "real"
    )
    assert manifest["count"] == 19
    assert manifest["added"] == 19
    assert manifest["validation"]["violations"] == 1
    assert manifest["validation"]["passed"] == 19
    assert manifest["rejected"] == 1


def test_reject_rate_over_threshold_aborts(tmp_path: Path) -> None:
    records = [_rel("K1"), _rel("K2"), _rel("K3"), {"x": 1}, {"y": 2}]  # 2/5 = 40%
    with pytest.raises(ReviewLandingError, match="reject rate"):
        land_review_triples(_ListReader(records, "relation"), "20260720", real_dir=tmp_path / "real")


# ---------------------------------------------------------------------------
# Cumulative snapshot landing
# ---------------------------------------------------------------------------

def test_first_landing_all_added(tmp_path: Path) -> None:
    snap, manifest = land_review_triples(
        _ListReader([_rel("K1"), _rel("K2")], "relation"), "20260720", real_dir=tmp_path / "real"
    )
    assert manifest["added"] == 2 and manifest["unchanged"] == 0 and manifest["count"] == 2
    assert manifest["carried_forward"] == 0
    assert snap.name == "review_triples_relation_20260720.json"
    on_disk = json.loads(snap.read_text(encoding="utf-8"))
    assert sorted(r["source_review_key"] for r in on_disk) == ["K1", "K2"]


def test_relanding_same_input_all_unchanged_identical_snapshot(tmp_path: Path) -> None:
    real = tmp_path / "real"
    records = [_rel("K1"), _rel("K2")]
    snap1, _ = land_review_triples(_ListReader(records, "relation"), "20260720", real_dir=real)
    content1 = snap1.read_text(encoding="utf-8")
    snap2, manifest2 = land_review_triples(_ListReader(records, "relation"), "20260720", real_dir=real)
    assert snap2 == snap1
    assert snap2.read_text(encoding="utf-8") == content1  # byte-identical rewrite
    assert manifest2["unchanged"] == 2 and manifest2["added"] == 0
    assert manifest2["carried_forward"] == 0 and manifest2["count"] == 2


def test_new_records_added_trigger_full_rewrite(tmp_path: Path) -> None:
    real = tmp_path / "real"
    land_review_triples(_ListReader([_rel("K1"), _rel("K2"), _rel("K3")], "relation"),
                        "20260720", real_dir=real)
    snap2, manifest2 = land_review_triples(
        _ListReader([_rel("K4"), _rel("K5")], "relation"), "20260721", real_dir=real
    )
    assert manifest2["added"] == 2 and manifest2["unchanged"] == 0
    assert manifest2["carried_forward"] == 3 and manifest2["count"] == 5
    on_disk = json.loads(snap2.read_text(encoding="utf-8"))  # full corpus, not just new
    assert sorted(r["source_review_key"] for r in on_disk) == ["K1", "K2", "K3", "K4", "K5"]


def test_same_key_different_payload_hard_fails(tmp_path: Path) -> None:
    real = tmp_path / "real"
    land_review_triples(_ListReader([_rel("K1", "old")], "relation"), "20260720", real_dir=real)
    with pytest.raises(ReviewLandingError, match="different payload"):
        land_review_triples(_ListReader([_rel("K1", "new")], "relation"), "20260721", real_dir=real)


def test_allow_updates_reclassifies_conflict(tmp_path: Path) -> None:
    real = tmp_path / "real"
    land_review_triples(_ListReader([_rel("K1", "old")], "relation"), "20260720", real_dir=real)
    snap2, manifest2 = land_review_triples(
        _ListReader([_rel("K1", "new")], "relation"), "20260721", real_dir=real, allow_updates=True
    )
    assert manifest2["updated"] == 1 and manifest2["added"] == 0 and manifest2["count"] == 1
    on_disk = json.loads(snap2.read_text(encoding="utf-8"))
    assert on_disk[0]["text"] == "new"


def test_format_mix_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    land_review_triples(_ListReader([_rel("K1")], "relation"), "20260720", real_dir=real)
    with pytest.raises(ReviewLandingError, match="format mix"):
        land_review_triples(_ListReader([_rs("R1")], "rs_jsonl"), "20260721", real_dir=real)


def test_missing_previous_snapshot_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    snap1, _ = land_review_triples(_ListReader([_rel("K1")], "relation"), "20260720", real_dir=real)
    snap1.unlink()  # manifest still points here
    with pytest.raises(ReviewLandingError, match="missing snapshot"):
        land_review_triples(_ListReader([_rel("K2")], "relation"), "20260721", real_dir=real)


# ---------------------------------------------------------------------------
# Determinism / atomicity / privacy
# ---------------------------------------------------------------------------

def test_landing_is_deterministic_and_0600(tmp_path: Path) -> None:
    r1, r2 = tmp_path / "r1", tmp_path / "r2"
    records = [_rel("K2"), _rel("K1")]  # unsorted input → sorted output
    p1, _ = land_review_triples(_ListReader(records, "relation"), "20260720", real_dir=r1)
    p2, _ = land_review_triples(_ListReader(list(reversed(records)), "relation"), "20260720", real_dir=r2)
    assert p1.read_text(encoding="utf-8") == p2.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(p1).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(p1.parent).st_mode) == 0o700


def test_manifest_has_no_review_text(tmp_path: Path) -> None:
    _snap, manifest = land_review_triples(
        _ListReader([_rel("K1", "은밀한리뷰텍스트")], "relation"), "20260720", real_dir=tmp_path / "real"
    )
    assert "은밀한리뷰텍스트" not in json.dumps(manifest, ensure_ascii=False)


def test_rs_jsonl_lands_as_line_delimited(tmp_path: Path) -> None:
    snap, manifest = land_review_triples(
        _ListReader([_rs("R1"), _rs("R2")], "rs_jsonl"), "20260720", real_dir=tmp_path / "real"
    )
    assert snap.name == "review_triples_rs_jsonl_20260720.jsonl"
    assert manifest["key_field"] == "id"
    lines = [ln for ln in snap.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2 and all(json.loads(ln)["id"] for ln in lines)


# ---------------------------------------------------------------------------
# e2e: landed snapshot consumed by the real entry points
# ---------------------------------------------------------------------------

def test_e2e_relation_landing_consumed_by_full_load(tmp_path: Path) -> None:
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    reader = FileReader(MOCK / "review_triples_raw.json", "relation")
    snap, manifest = land_review_triples(reader, "20260720", real_dir=tmp_path / "real")
    assert manifest["count"] == 906  # full corpus landed

    result = run_full_load(FullLoadConfig(
        review_json_path=str(snap), product_es_records=[], user_profiles={},
        review_format="relation", kg_mode="off", max_reviews=25,
    ))
    assert result.review_count == 25  # landed relation snapshot is loader-consumable


def test_e2e_rs_landing_consumed_by_full_load(tmp_path: Path) -> None:
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    reader = FileReader(MOCK / "review_rs_samples.json", "rs_jsonl")
    snap, manifest = land_review_triples(reader, "20260720", real_dir=tmp_path / "real")
    assert manifest["count"] == 20

    result = run_full_load(FullLoadConfig(
        review_json_path=str(snap), product_es_records=[], user_profiles={},
        review_format="rs_jsonl", kg_mode="off",
    ))
    assert result.review_count == 20


@pytest.mark.asyncio
async def test_e2e_rs_landing_consumed_by_demo_pipeline_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.web import server

    reader = FileReader(MOCK / "review_rs_samples.json", "rs_jsonl")
    snap, _ = land_review_triples(reader, "20260720", real_dir=tmp_path / "real")

    monkeypatch.setenv("GRAPHRAPPING_ENABLE_PIPELINE_RUN", "1")
    monkeypatch.delenv("GRAPHRAPPING_PIPELINE_RUN_TOKEN", raising=False)
    monkeypatch.setenv("GRAPHRAPPING_REVIEW_TRIPLES_JSON", str(snap))  # IC-1 env wiring

    result = await server.pipeline_run(
        server.PipelineRunRequest(review_format="rs_jsonl", max_reviews=20)
    )
    assert result["reviews"] == 20  # demo consumed the landed rs_jsonl snapshot via env
