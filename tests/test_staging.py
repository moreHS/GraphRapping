"""Shared staging-helper tests (IC-1 / plan §2·§6).

Covers the protections extracted from the user-profile backfill script:
0700 dirs, 0600 atomic writes, symlink refusal, one-subdirectory-deep path
guard, injected-date determinism, and manifest structure.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from src.ingest import staging


# ---------------------------------------------------------------------------
# Directory + filename conventions
# ---------------------------------------------------------------------------

def test_ensure_staging_dir_creates_0700_per_kind(tmp_path: Path) -> None:
    real = tmp_path / "real"
    for kind in ("users", "reviews", "products"):
        target = staging.ensure_staging_dir(kind, real_dir=real)
        assert target == real / kind
        assert target.is_dir()
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(real).st_mode) == 0o700


def test_staging_dir_unknown_kind_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown staging kind"):
        staging.staging_dir("caches", real_dir=tmp_path)


def test_snapshot_filename_injected_date_is_deterministic() -> None:
    assert staging.snapshot_filename("user_profiles_real", "20260720") == (
        "user_profiles_real_20260720.json"
    )
    assert staging.snapshot_filename("reviews", "20260720", ext="jsonl") == "reviews_20260720.jsonl"
    # Same inputs → same name (no Date.now anywhere).
    assert staging.snapshot_filename("x", "20260101") == staging.snapshot_filename("x", "20260101")


def test_snapshot_filename_rejects_non_yyyymmdd() -> None:
    for bad in ("2026-07-20", "202607", "notadate", ""):
        with pytest.raises(ValueError, match="YYYYMMDD"):
            staging.snapshot_filename("x", bad)


# ---------------------------------------------------------------------------
# Path guard: real_dir-direct OR one subdirectory deep; symlink-safe
# ---------------------------------------------------------------------------

def test_validate_staging_path_accepts_direct_and_one_subdir(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    assert staging.validate_staging_path(real / "f.json", real_dir=real).parent == real.resolve()
    sub = staging.validate_staging_path(real / "users" / "f.json", real_dir=real)
    assert sub.parent == (real / "users").resolve()


def test_validate_staging_path_rejects_outside_and_too_deep(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    with pytest.raises(ValueError, match="inside"):
        staging.validate_staging_path(tmp_path / "elsewhere.json", real_dir=real)
    with pytest.raises(ValueError, match="inside"):  # two levels deep rejected
        staging.validate_staging_path(real / "users" / "a" / "f.json", real_dir=real)
    with pytest.raises(ValueError, match="inside"):  # .. escape
        staging.validate_staging_path(real / ".." / "escape.json", real_dir=real)


def test_validate_staging_path_rejects_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = real / "link.json"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        staging.validate_staging_path(link, real_dir=real)

    linked_real = tmp_path / "linked_real"
    linked_real.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        staging.validate_staging_path(linked_real / "f.json", real_dir=linked_real)


def test_validate_staging_path_rejects_symlinked_subdir_escape(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (real / "users").symlink_to(external)  # subdir symlink pointing outside real
    with pytest.raises(ValueError, match="inside"):
        staging.validate_staging_path(real / "users" / "f.json", real_dir=real)


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def test_write_json_atomic_modes_content_and_no_leftover(tmp_path: Path) -> None:
    target = tmp_path / "real" / "snap.json"
    staging.write_json_atomic(target, '{"a": 1}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(target.parent).st_mode) == 0o700
    assert [p.name for p in target.parent.iterdir()] == ["snap.json"]


def test_write_json_atomic_cleans_temp_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "real" / "snap.json"
    target.parent.mkdir(parents=True)

    def _boom(*_a, **_k):
        raise RuntimeError("replace failed")

    monkeypatch.setattr(staging.os, "replace", _boom)
    with pytest.raises(RuntimeError, match="replace failed"):
        staging.write_json_atomic(target, "{}")
    # No .tmp_* leftovers.
    assert list(target.parent.iterdir()) == []


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def test_manifest_to_dict_and_write(tmp_path: Path) -> None:
    manifest = staging.StagingManifest(
        path=str(tmp_path / "real" / "users" / "snap.json"),
        format="user_profiles_normalized",
        count=3,
        generated_at="20260720",
        added=3,
        validation={"passed": 3, "total": 3, "violations": 0, "violations_top": []},
        extra={"backfill_stats": {"rows_fetched": 3}},
    )
    data = manifest.to_dict()
    assert data["count"] == 3
    assert data["generated_at"] == "20260720"
    assert data["added"] == 3 and data["updated"] == 0 and data["conflict"] == 0
    assert data["validation"]["violations"] == 0
    assert data["backfill_stats"] == {"rows_fetched": 3}

    manifest_path = tmp_path / "real" / "users" / "manifest.json"
    staging.write_manifest(manifest_path, manifest)
    assert stat.S_IMODE(os.stat(manifest_path).st_mode) == 0o600
    reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert reloaded == data
