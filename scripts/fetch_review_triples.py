#!/usr/bin/env python3
"""Review-triple connector — reader interface + file backend + cumulative landing.

IC-R (fable_doc/plans/2026-07-19_input_connectors_readiness.md §4). Prepares the
"real DB" transition for the review-triples source WITHOUT touching the pipeline:
a connector reads review triples from a backend, validates them against the
matching input contract, and lands the FULL corpus as a dated snapshot under the
git-ignored ``mockdata/real/reviews/`` tree. The demo / full-load entry points
consume the snapshot by pointing ``GRAPHRAPPING_REVIEW_TRIPLES_JSON`` at it
(IC-1 wired the env resolution; ``rs_jsonl`` snapshots are consumed with
``review_format="rs_jsonl"``, ``relation`` snapshots with the default).

TWO FORMATS (plan §1 / codex #1)
--------------------------------
* ``rs_jsonl`` — raw S3 operational output (``id`` / ``date`` / ``product_id`` /
  ``ner_spans`` / ``bee_spans`` + top-level demographics). Key = ``id``.
* ``relation`` — the current landing JSON (``source_review_key`` / ``drup_dt`` /
  ``source_product_id`` / ``ner`` / ``bee`` / ``relation`` + nested
  ``reviewer_profile``). Key = ``source_review_key``.
The two are DIFFERENT shapes for the same information; the connector never
converts between them — it lands the input shape as-is and the consumer selects
the loader via ``review_format``. The RS↔Relation field map lives in
``src.ingest.input_contracts`` as the single source of truth.

CUMULATIVE SNAPSHOT LANDING (codex #3)
--------------------------------------
full-load / demo ALWAYS consume the entire corpus, so a landing run never writes
"new records only". Each run loads the previous landing snapshot (found via the
``reviews/manifest.json`` pointer), merges the new input keyed by the format's
review key, and rewrites the ENTIRE corpus as a new dated snapshot atomically —
no partial file is ever produced. The manifest records ``added`` / ``updated`` /
``unchanged`` / ``conflict`` plus ``carried_forward`` (existing keys untouched by
this batch). A same-key record whose payload differs is a **hard failure** by
default (no silent drop, no silent overwrite); ``--allow-updates`` reclassifies
it as an update. Landing a format different from the existing snapshot's format
is refused (format-mixing guard via the manifest ``format`` field).

BACKENDS
--------
Only the local :class:`FileReader` ships now (plan: source access is confirmed in
IC-3). Any backend implementing :class:`ReviewTripleReader` (S3 via boto3,
Snowflake via its connector) is accepted by :func:`land_review_triples` — the
cumulative-landing logic is backend-agnostic. Credentials MUST come from the
standard env conventions (AWS: ``AWS_*`` env / shared profile; Snowflake:
``SNOWFLAKE_*``); hardcoding credentials in this repo is forbidden (mirrors the
user-profile backfill privacy rules). File→DB incremental adaptation is a
separate follow-up track (codex #3): landing is responsible only for supplying a
complete snapshot.

Usage:
    python scripts/fetch_review_triples.py --input <file|dir> --format relation
    python scripts/fetch_review_triples.py --input rs.jsonl --format rs_jsonl --date 20260720
"""

from __future__ import annotations

import abc
import argparse
import datetime as _dt
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# ── Paths ──
GRAPHRAPPING_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_DIR = GRAPHRAPPING_ROOT / "mockdata" / "real"

# Direct-run safety: put the repo root on sys.path so the lazy ``src.ingest.*``
# imports resolve when invoked as ``python scripts/fetch_review_triples.py``
# (sys.path[0] would otherwise be the scripts/ dir). Harmless under pytest.
if str(GRAPHRAPPING_ROOT) not in sys.path:
    sys.path.insert(0, str(GRAPHRAPPING_ROOT))

# Supported review formats → the review key used for cumulative merge.
KEY_FIELD_BY_FORMAT: dict[str, str] = {
    "rs_jsonl": "id",
    "relation": "source_review_key",
}
# Output extension per format (rs.jsonl is line-delimited by nature; relation
# landing is a JSON array like the current fixture). Both loaders auto-detect,
# so either is consumable — the extension just keeps the on-disk shape faithful.
_OUTPUT_EXT_BY_FORMAT: dict[str, str] = {"rs_jsonl": "jsonl", "relation": "json"}

# Reject-rate ceiling: if more than this fraction of input records fail the
# contract, the whole landing run aborts (rather than landing a mostly-broken
# corpus). Individual violations below the ceiling are dropped + aggregated.
REJECT_THRESHOLD = 0.10


class ReviewLandingError(RuntimeError):
    """A landing run was refused (threshold exceeded, format mix, or conflict)."""


# =============================================================================
# Reader interface + file backend
# =============================================================================

class ReviewTripleReader(abc.ABC):
    """Backend-agnostic review-triple source.

    Contract: :meth:`read` yields raw review-triple ``dict`` records (unvalidated
    — :func:`land_review_triples` validates), and :attr:`format` names the input
    contract / review key (``"rs_jsonl"`` or ``"relation"``). Future backends (S3
    via boto3, Snowflake via its connector) implement this same interface;
    credentials come from standard env conventions (``AWS_*`` / ``SNOWFLAKE_*``)
    and are never hardcoded.
    """

    @property
    @abc.abstractmethod
    def format(self) -> str:
        """Input format / contract name — one of ``KEY_FIELD_BY_FORMAT``."""

    @abc.abstractmethod
    def read(self) -> Iterator[dict[str, Any]]:
        """Yield raw review-triple records (dicts)."""


class FileReader(ReviewTripleReader):
    """Local-file backend: a ``.json`` (JSON array) / ``.jsonl`` (line-delimited)
    file, or a directory of such files (read in sorted filename order).

    The ``format`` is independent of the extension: e.g. the raw sample fixture
    ``review_rs_samples.json`` is a ``.json`` array whose records are the
    ``rs_jsonl`` shape, so ``FileReader(path, "rs_jsonl")`` parses it as a JSON
    array but validates it against the raw rs.jsonl contract.
    """

    _SUFFIXES = (".json", ".jsonl")

    def __init__(self, path: str | Path, fmt: str) -> None:
        if fmt not in KEY_FIELD_BY_FORMAT:
            raise ValueError(
                f"unknown format: {fmt!r} (expected one of {sorted(KEY_FIELD_BY_FORMAT)})"
            )
        self._path = Path(path)
        self._format = fmt

    @property
    def format(self) -> str:
        return self._format

    def _resolve_files(self) -> list[Path]:
        if self._path.is_dir():
            return sorted(p for p in self._path.iterdir() if p.suffix in self._SUFFIXES)
        if not self._path.exists():
            raise ReviewLandingError(f"input path not found: {self._path}")
        return [self._path]

    def read(self) -> Iterator[dict[str, Any]]:
        for file_path in self._resolve_files():
            content = file_path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            if file_path.suffix == ".jsonl" and not content.startswith("["):
                for line in content.splitlines():
                    line = line.strip()
                    if line:
                        yield json.loads(line)
                continue
            data = json.loads(content)
            if isinstance(data, list):
                yield from data
            elif isinstance(data, dict):
                yield data
            else:
                raise ReviewLandingError(
                    f"{file_path}: expected a JSON array/object or JSONL lines"
                )


# =============================================================================
# Cumulative landing (pure w.r.t. the clock — date is injected)
# =============================================================================

def _canonical(record: dict[str, Any]) -> str:
    """Order-insensitive payload fingerprint (dict-key order ignored, list order
    preserved) used to decide unchanged vs. changed for a same-key record."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def _load_existing_corpus(
    manifest_path: Path, fmt: str, key_field: str
) -> dict[str, dict[str, Any]]:
    """Load the previous landing snapshot via the manifest pointer.

    Returns ``{}`` when there is no prior landing. Refuses a format switch
    (format-mixing guard) and refuses to proceed if the manifest points at a
    snapshot file that is gone (never silently discard an accumulated corpus).
    """
    if not manifest_path.exists():
        return {}
    prev = json.loads(manifest_path.read_text(encoding="utf-8"))
    prev_format = prev.get("format")
    if prev_format != fmt:
        raise ReviewLandingError(
            f"format mix refused: existing landing is {prev_format!r}, "
            f"requested {fmt!r} (re-land under the existing format or reset the dir)"
        )
    prev_path = Path(prev["path"])
    if not prev_path.exists():
        raise ReviewLandingError(
            f"manifest points at a missing snapshot: {prev_path} "
            "(refusing to silently start a fresh corpus)"
        )
    reader = FileReader(prev_path, fmt)
    existing: dict[str, dict[str, Any]] = {}
    for record in reader.read():
        existing[str(record.get(key_field))] = record
    return existing


def land_review_triples(
    reader: ReviewTripleReader,
    date_str: str,
    *,
    real_dir: Path = REAL_DATA_DIR,
    allow_updates: bool = False,
    reject_threshold: float = REJECT_THRESHOLD,
) -> tuple[Path, dict[str, Any]]:
    """Validate ``reader``'s records and land the full merged corpus (§4 / codex #3).

    Steps: read → per-record contract validation (drop + aggregate violations;
    abort if the reject rate exceeds ``reject_threshold``) → merge into the
    previous corpus keyed by the format's review key → atomically rewrite the
    whole corpus as a dated snapshot + manifest. A same-key/different-payload
    record hard-fails unless ``allow_updates`` is set. Returns
    ``(snapshot_path, manifest_dict)``. ``date_str`` is caller-injected (no
    ``date.now``), so the output is deterministic.
    """
    from src.ingest import input_contracts, staging

    fmt = reader.format
    if fmt not in KEY_FIELD_BY_FORMAT:
        raise ValueError(f"unsupported reader format: {fmt!r}")
    key_field = KEY_FIELD_BY_FORMAT[fmt]

    validator_by_format = {
        "rs_jsonl": input_contracts.validate_rs_jsonl_record,
        "relation": input_contracts.validate_relation_landing_record,
    }
    validator = validator_by_format[fmt]

    records = list(reader.read())
    total = len(records)
    report = input_contracts.validate_records(records, fmt)
    accepted = [r for r in records if isinstance(r, dict) and not validator(r)]
    rejected = report.violations
    reject_rate = (rejected / total) if total else 0.0
    if total and reject_rate > reject_threshold:
        raise ReviewLandingError(
            f"contract reject rate {reject_rate:.1%} exceeds ceiling "
            f"{reject_threshold:.0%} ({rejected}/{total} records) — landing aborted. "
            f"top reasons: {report.violations_top[:3]}"
        )

    reviews_dir = staging.ensure_staging_dir("reviews", real_dir=real_dir)
    manifest_path = reviews_dir / "manifest.json"
    existing = _load_existing_corpus(manifest_path, fmt, key_field)

    corpus = dict(existing)
    incoming_keys: set[str] = set()
    added = updated = unchanged = 0
    conflicts: list[str] = []
    for record in accepted:
        key = str(record.get(key_field))
        incoming_keys.add(key)
        if key not in existing:
            corpus[key] = record
            added += 1
        elif _canonical(record) == _canonical(existing[key]):
            unchanged += 1
        elif allow_updates:
            corpus[key] = record
            updated += 1
        else:
            conflicts.append(key)

    if conflicts:
        sample = conflicts[:10]
        raise ReviewLandingError(
            f"{len(conflicts)} same-key record(s) carry a different payload than the "
            f"existing landing (hard-fail; pass --allow-updates to accept as updates). "
            f"sample keys: {sample}"
        )

    carried_forward = sum(1 for k in existing if k not in incoming_keys)
    ordered = [corpus[k] for k in sorted(corpus)]
    ext = _OUTPUT_EXT_BY_FORMAT[fmt]
    filename = staging.snapshot_filename(f"review_triples_{fmt}", date_str, ext=ext)
    snapshot_path = staging.validate_staging_path(reviews_dir / filename, real_dir=real_dir)
    staging.write_json_atomic(snapshot_path, _serialize(ordered, ext))

    manifest = staging.StagingManifest(
        path=str(snapshot_path),
        format=fmt,
        count=len(corpus),
        generated_at=date_str,
        added=added,
        updated=updated,
        unchanged=unchanged,
        conflict=0,
        validation=report.to_manifest_dict(),
        extra={
            "key_field": key_field,
            "carried_forward": carried_forward,
            "rejected": rejected,
            "reject_rate": round(reject_rate, 4),
            "reject_threshold": reject_threshold,
            "allow_updates": allow_updates,
        },
    )
    staging.write_manifest(manifest_path, manifest)
    return snapshot_path, manifest.to_dict()


def _serialize(records: list[dict[str, Any]], ext: str) -> str:
    if ext == "jsonl":
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    return json.dumps(records, ensure_ascii=False, indent=2) + "\n"


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="local .json/.jsonl file or a directory of them")
    parser.add_argument("--format", required=True, choices=sorted(KEY_FIELD_BY_FORMAT),
                        help="input contract / review key (rs_jsonl or relation)")
    parser.add_argument("--date", default=None,
                        help="snapshot date YYYYMMDD (default: today)")
    parser.add_argument("--allow-updates", action="store_true",
                        help="accept same-key/different-payload records as updates "
                             "(default: hard-fail on conflict)")
    args = parser.parse_args()

    date_str = args.date or _dt.datetime.now().strftime("%Y%m%d")
    reader = FileReader(args.input, args.format)
    snapshot_path, manifest = land_review_triples(
        reader, date_str, allow_updates=args.allow_updates
    )

    print("=== REVIEW LANDING (aggregates only) ===")
    print(json.dumps({
        "format": manifest["format"],
        "count": manifest["count"],
        "added": manifest["added"],
        "updated": manifest["updated"],
        "unchanged": manifest["unchanged"],
        "carried_forward": manifest["carried_forward"],
        "rejected": manifest["rejected"],
        "reject_rate": manifest["reject_rate"],
        "validation": manifest["validation"],
    }, ensure_ascii=False, indent=2))
    print(f"\nLanded {manifest['count']} review triples → {snapshot_path} (mode 0600)")


if __name__ == "__main__":
    main()
