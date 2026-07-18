#!/usr/bin/env python3
"""Fetch real (pseudonymized) user profiles with purchase history from Azure PG.

Purchase-history backfill (fable_doc/08 §C1). Reads the personalization view
``agent.aibe_user_context_mstr_v`` for rows that carry representative product
codes (``rprs_prd_cd``), normalizes each via the personalization agent's
``_normalize_profile`` (reused verbatim), resolves the codes against the wide
product catalog (``REPRESENTATIVE_PROD_CODE`` == ``variant_family_id``), and
embeds them as ``purchase_events`` on each profile. The output feeds
GraphRapping's *existing* ``derive_purchase_features`` path (OWNS_PRODUCT /
OWNS_FAMILY facts → G4 ``similar_product_affinity`` boost).

EVENT SEMANTICS (cross-review P0-3)
-----------------------------------
One purchase *occurrence* = one PurchaseEvent. An occurrence is a distinct
(rprs_prd_cd, purchase date) pair extracted from the raw summaries (a code with
no date at all counts as a single dateless occurrence). The event's
``product_id`` is the *deterministic representative member* (sorted-first SKU of
the family) — member SKUs are NEVER expanded into events, because N members
would inflate ``family_purchase_count`` and fabricate REPURCHASES_FAMILY /
REPURCHASES_BRAND facts from a single purchase. Family-level ownership coverage
is handled downstream by ``family_lookup`` → OWNS_FAMILY (existing path).

PRIVACY / SAFETY
----------------
* Live read-only DB tool — NOT run in CI. Unit tests exercise the pure helpers
  with mock rows only.
* Credentials are read at runtime from the personalization agent ``.env`` by
  *path reference only* (never copied into this repo) and never logged.
* user_id is pseudonymized to ``real_<first 12 chars of incs_no>`` (incs_no is
  itself an already-hashed value). Rows with a missing/blank/short incs_no are
  skipped (counted); a 12-char prefix collision aborts the whole run.
* Output is confined to the git-ignored ``mockdata/real/`` directory
  (``--output`` outside it is rejected; symlinks rejected; atomic tmp→rename
  write with file mode 0600). stdout reports AGGREGATES ONLY — no row-level
  user/product combinations.
* SELECT-only inside a read-only transaction, ``ORDER BY incs_no LIMIT $1``
  (deterministic), ``--limit`` hard-capped at 500.

Usage:
    python scripts/fetch_user_profiles_pg.py --limit 50
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import logging
import os
import re
import sys
import tempfile
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

# ── Paths ──
GRAPHRAPPING_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA_DIR = GRAPHRAPPING_ROOT / "mockdata" / "real"
DEFAULT_OUTPUT = REAL_DATA_DIR / "user_profiles_real_normalized.json"
DEFAULT_CATALOG = GRAPHRAPPING_ROOT / "mockdata" / "product_catalog_es.json"
DEFAULT_PA_SRC = Path("/Users/amore/workplace/agent-aibc/persnal-agent/src")
DEFAULT_ENV_FILE = Path("/Users/amore/workplace/agent-aibc/persnal-agent/.env")

VIEW = "agent.aibe_user_context_mstr_v"
MAX_LIMIT = 500

# Representative product codes are 9-digit numerics (session-measured; e.g.
# "131172879"). Enforced on BOTH catalog indexing and raw-profile extraction;
# non-conforming codes are dropped and reported as aggregate counts.
REP_CODE_RE = re.compile(r"^[0-9]{9}$")

# Read-only backfill query. All three raw summary sources that can carry
# rprs_prd_cd are predicated (purchase_profile / repurchase_category_affinity /
# seasonal_affinity) — extraction supports all three, so the WHERE must too.
BACKFILL_QUERY = f"""
    SELECT incs_no,
           user_profile, skin_profile, purchase_profile, brand_affinity,
           repurchase_category_affinity, seasonal_affinity,
           chat_summary AS profile_from_chathistory
    FROM {VIEW}
    WHERE repurchase_category_affinity::text LIKE '%rprs_prd_cd%'
       OR purchase_profile::text LIKE '%rprs_prd_cd%'
       OR seasonal_affinity::text LIKE '%rprs_prd_cd%'
    ORDER BY incs_no
    LIMIT $1
"""

# Raw-JSON summary sources that carry rprs_prd_cd (the personalization
# normalizer drops the codes, so they must be read from the raw columns).
#   (column, summary key inside that column's JSON, preferred date field)
_SUMMARY_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("purchase_profile", "use_expected_product_summary", "purchase_date"),
    ("repurchase_category_affinity", "preferred_repurchase_product_summary", "recent_purchase_date"),
    ("seasonal_affinity", "seasonal_product_summary", "purchase_date"),
)

_PSEUDONYM_PREFIX_LEN = 12


# =============================================================================
# Pure helpers (unit-tested with mock rows; no DB / no personalization import)
# =============================================================================

def build_rep_code_index(
    catalog_records: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], int]:
    """Catalog → (``{rep_code: sorted[member SKUs]}``, skipped-record count).

    The representative code IS the serving ``variant_family_id``; its member
    SKUs are the product_master ids used by ``derive_purchase_features``'s
    ``family_lookup`` (SKU → variant_family_id). Records whose rep code is
    missing or fails the 9-digit rule (or whose SKU is missing) are skipped
    and counted.
    """
    index: dict[str, list[str]] = {}
    skipped = 0
    for rec in catalog_records:
        if not isinstance(rec, Mapping):
            skipped += 1
            continue
        rep = rec.get("REPRESENTATIVE_PROD_CODE")
        sku = rec.get("ONLINE_PROD_SERIAL_NUMBER")
        rep_s = str(rep).strip() if rep is not None else ""
        sku_s = str(sku).strip() if sku is not None else ""
        if not REP_CODE_RE.match(rep_s) or not sku_s:
            skipped += 1
            continue
        members = index.setdefault(rep_s, [])
        if sku_s not in members:
            members.append(sku_s)
    for members in index.values():
        members.sort()
    return index, skipped


def _walk_summary_products(summary: Any):
    """Yield product dicts from a ``{midcat: {subcat: [products]}}`` summary.

    Defensive against depth variations: recurse through mappings, yield dicts
    found inside lists (the product leaves).
    """
    if isinstance(summary, Mapping):
        for value in summary.values():
            yield from _walk_summary_products(value)
    elif isinstance(summary, list):
        for item in summary:
            if isinstance(item, Mapping):
                yield item


def extract_purchase_codes(
    raw: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Extract ``rprs_prd_cd`` occurrences from a raw (pre-normalization) row.

    Returns ``({rprs_prd_cd: {"dates": set[str], "kinds": set[str]}}, invalid_codes)``.
    Codes failing the 9-digit rule land in ``invalid_codes`` (aggregate-reported,
    never resolved). Reads the raw JSON columns directly because
    ``_normalize_profile`` strips the codes.
    """
    codes: dict[str, dict[str, Any]] = {}
    invalid: set[str] = set()
    for column, summary_key, date_field in _SUMMARY_SOURCES:
        col_data = raw.get(column)
        if not isinstance(col_data, Mapping):
            continue
        for product in _walk_summary_products(col_data.get(summary_key)):
            code = product.get("rprs_prd_cd")
            if code is None:
                continue
            code_s = str(code).strip()
            if not code_s:
                continue
            if not REP_CODE_RE.match(code_s):
                invalid.add(code_s)
                continue
            entry = codes.setdefault(code_s, {"dates": set(), "kinds": set()})
            date = (
                product.get(date_field)
                or product.get("purchase_date")
                or product.get("recent_purchase_date")
            )
            if date:
                entry["dates"].add(str(date).strip())
            entry["kinds"].add(summary_key)
    return codes, invalid


def resolve_purchase_events(
    user_id: str,
    codes: Mapping[str, Mapping[str, Any]],
    rep_index: Mapping[str, list[str]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Resolve codes → PurchaseEvent-shaped dicts. One occurrence = one event.

    Occurrence := a distinct (code, date) pair; a code with no dates at all is
    one dateless occurrence. ``product_id`` is the deterministic representative
    member (sorted-first SKU of the family) — members are NOT expanded into
    events (that would inflate ``family_purchase_count`` in
    ``derive_purchase_features`` and fabricate REPURCHASES_FAMILY /
    REPURCHASES_BRAND facts from a single purchase; family-level ownership is
    covered by ``family_lookup`` → OWNS_FAMILY downstream). Codes with no
    catalog match are dropped and reported. Deterministic output.
    """
    events: list[dict[str, Any]] = []
    matched_families: set[str] = set()
    anchor_skus: set[str] = set()
    dropped: set[str] = set()
    for code in sorted(codes):
        members = rep_index.get(code)
        if not members:
            dropped.add(code)
            continue
        matched_families.add(code)
        rep_sku = members[0]  # sorted-first member = deterministic anchor
        anchor_skus.add(rep_sku)
        dates = sorted(str(d) for d in (codes[code].get("dates") or set()))
        occurrences: list[str | None] = list(dates) if dates else [None]
        for purchased_at in occurrences:
            events.append(
                {
                    "purchase_event_id": f"{user_id}::{rep_sku}::{purchased_at or 'na'}",
                    "product_id": rep_sku,
                    "purchased_at": purchased_at,
                    "quantity": 1,
                }
            )
    events.sort(key=lambda e: (e["product_id"], e["purchased_at"] or ""))
    stats = {
        "matched_families": sorted(matched_families),
        "anchor_skus": sorted(anchor_skus),
        "dropped_codes": sorted(dropped),
    }
    return events, stats


def pseudonymize_incs_no(incs_no: Any) -> str:
    """Demo user id: ``real_<first 12 chars of the already-hashed incs_no>``."""
    return f"real_{str(incs_no)[:_PSEUDONYM_PREFIX_LEN]}"


def register_pseudonym(incs_no: Any, prefix_map: dict[str, str]) -> str | None:
    """Validate incs_no and register its 12-char pseudonym prefix.

    Returns the pseudonymized user_id, or ``None`` when incs_no is missing,
    blank, or shorter than the prefix (caller skips + counts the row).
    Raises ``RuntimeError`` on a prefix collision between two DIFFERENT
    incs_no values — the run must abort rather than silently merge two users.
    """
    value = str(incs_no).strip() if incs_no is not None else ""
    if len(value) < _PSEUDONYM_PREFIX_LEN:
        return None
    prefix = value[:_PSEUDONYM_PREFIX_LEN]
    existing = prefix_map.get(prefix)
    if existing is not None and existing != value:
        raise RuntimeError(
            "pseudonym prefix collision: two distinct incs_no values share the "
            f"same {_PSEUDONYM_PREFIX_LEN}-char prefix — aborting. Widen "
            "_PSEUDONYM_PREFIX_LEN before re-running."
        )
    prefix_map[prefix] = value
    return f"real_{prefix}"


def validate_output_path(output: Path, real_dir: Path = REAL_DATA_DIR) -> Path:
    """Confine ``--output`` to the git-ignored real-data directory.

    Rejects paths outside ``real_dir`` (after resolving ``..`` and symlinks),
    rejects a symlinked output file, and rejects a symlinked ``real_dir``
    itself (filename/dirname drift is what the directory-level .gitignore
    defends against — a symlink would silently defeat it).
    """
    if real_dir.exists() and real_dir.is_symlink():
        raise ValueError(f"real-data dir must not be a symlink: {real_dir}")
    candidate = output if output.is_absolute() else Path.cwd() / output
    if candidate.is_symlink():
        raise ValueError(f"--output must not be a symlink: {output}")
    resolved = candidate.resolve()
    if resolved.parent != real_dir.resolve():
        raise ValueError(
            f"--output must be a file directly inside {real_dir} "
            f"(git-ignored real-data dir); got: {output}"
        )
    return resolved


def write_output_atomic(path: Path, payload: str) -> None:
    """Atomic write (tmp → rename) with file mode 0600 and dir mode 0700."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp_real_profiles_", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.chmod(tmp_name, 0o600)  # mkstemp default is 0600; explicit for clarity
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


# =============================================================================
# Runtime-only helpers (DB creds, personalization normalizer)
# =============================================================================

def load_db_credentials(env_path: Path) -> dict[str, str]:
    """Parse AIBE_DB_* keys from the personalization .env (values never logged)."""
    if not env_path.exists():
        raise FileNotFoundError(f"env file not found (credential source): {env_path}")
    creds: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("AIBE_DB"):
            creds[key] = value.strip().strip('"').strip("'")
    missing = [k for k in ("AIBE_DB_URL", "AIBE_DB_NM", "AIBE_DB_USER", "AIBE_DB_PW") if not creds.get(k)]
    if missing:
        raise ValueError(f"missing required DB credentials in {env_path}: {missing}")
    return creds


def load_personalization_normalizer(
    pa_src: Path,
) -> tuple[Callable[[dict[str, Any]], dict[str, Any]], Callable[[str, Any], Any], tuple[str, ...]]:
    """Load ``_normalize_profile`` / ``_parse_column`` / ``_PROFILE_COLUMNS`` via importlib.

    Mirrors scripts/sync_user_profiles.py: loads only the needed personalization
    files without importing the whole package. Stubs the one absolute dependency
    (``src.common.custom_logger``) so it resolves regardless of the caller's cwd.
    """
    personalization_dir = pa_src / "personalization"
    if not personalization_dir.exists():
        raise FileNotFoundError(f"personalization dir not found: {personalization_dir}")

    if "src.common.custom_logger" not in sys.modules:
        stub = types.ModuleType("src.common.custom_logger")
        stub.get_logger = lambda name=None: logging.getLogger(  # type: ignore[attr-defined]
            name if isinstance(name, str) else "personalization"
        )
        sys.modules["src.common.custom_logger"] = stub

    def _load(name: str, path: Path) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    date_utils = _load("personalization.date_utils", personalization_dir / "date_utils.py")
    pkg = types.ModuleType("personalization")
    pkg.__path__ = [str(personalization_dir)]  # type: ignore[attr-defined]
    pkg.date_utils = date_utils  # type: ignore[attr-defined]
    sys.modules["personalization"] = pkg
    data_store = _load("personalization.data_store", personalization_dir / "data_store.py")

    return (
        data_store._normalize_profile,
        data_store._parse_column,
        tuple(data_store._PROFILE_COLUMNS),
    )


def build_profile_record(
    user_id: str,
    raw: dict[str, Any],
    rep_index: Mapping[str, list[str]],
    normalize_profile: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Row → (normalized profile with embedded purchase_events, resolution stats)."""
    codes, invalid_codes = extract_purchase_codes(raw)
    events, stats = resolve_purchase_events(user_id, codes, rep_index)
    profile = normalize_profile(raw)
    if events:
        profile["purchase_events"] = events
    stats["total_codes"] = sorted(codes)
    stats["invalid_codes"] = sorted(invalid_codes)
    return profile, stats


# =============================================================================
# DB query (async, runtime-only)
# =============================================================================

async def _fetch_rows(creds: dict[str, str], limit: int) -> tuple[list[Any], str]:
    """SELECT rows inside a read-only transaction. Returns (rows, ssl_mode_used).

    TLS: tries verify-full semantics first — ``ssl.create_default_context()``
    (CA-bundle trust + hostname verification; the libpq verify-full equivalent —
    asyncpg's literal ``ssl="verify-full"`` string instead demands
    ``~/.postgresql/root.crt``, which is not how this host is provisioned). If
    the local trust store cannot validate the Azure chain, falls back to
    ``ssl="require"`` (encrypted, chain unvalidated) with an explicit stderr
    note — see DECISIONS.
    """
    import ssl as ssl_mod

    import asyncpg  # local import: keeps module import test-safe (no DB dep)

    connect_kwargs: dict[str, Any] = {
        "host": creds["AIBE_DB_URL"],
        "port": int(creds.get("AIBE_DB_PORT") or 5432),
        "database": creds["AIBE_DB_NM"],
        "user": creds["AIBE_DB_USER"],
        "password": creds["AIBE_DB_PW"],
        "timeout": 30.0,          # connect timeout
        "command_timeout": 60.0,  # per-query timeout
    }
    schema = creds.get("AIBE_DB_SCHEMA")
    if schema:
        connect_kwargs["server_settings"] = {"search_path": schema}

    ssl_mode = "verify-full"
    try:
        verify_ctx = ssl_mod.create_default_context()  # CERT_REQUIRED + hostname check
        conn = await asyncpg.connect(ssl=verify_ctx, **connect_kwargs)
    except (ssl_mod.SSLError, OSError) as exc:
        print(
            f"  TLS verify-full failed ({type(exc).__name__}) — falling back to "
            "ssl='require' (encrypted, chain unvalidated)",
            file=sys.stderr,
        )
        ssl_mode = "require"
        conn = await asyncpg.connect(ssl="require", **connect_kwargs)
    try:
        async with conn.transaction(readonly=True):
            rows: list[Any] = await conn.fetch(BACKFILL_QUERY, limit)
        return rows, ssl_mode
    finally:
        await conn.close()


# =============================================================================
# Main
# =============================================================================

def _limit_type(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"--limit must be between 1 and {MAX_LIMIT}")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=_limit_type, default=50,
                        help=f"max rows (default 50, hard cap {MAX_LIMIT})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"output file — must live inside {REAL_DATA_DIR}")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--pa-src", type=Path, default=DEFAULT_PA_SRC)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    import asyncio

    output_path = validate_output_path(args.output)

    print("LIVE DB READ (SELECT-only, read-only transaction) — not for CI.")
    print(f"  view={VIEW}  limit={args.limit}")
    print(f"  credentials (path-ref only): {args.env_file}")

    creds = load_db_credentials(args.env_file)
    normalize_profile, parse_column, profile_columns = load_personalization_normalizer(args.pa_src)

    catalog_records = json.loads(args.catalog.read_text(encoding="utf-8"))
    rep_index, catalog_skipped = build_rep_code_index(catalog_records)
    print(
        f"  catalog: {len(catalog_records)} records, {len(rep_index)} representative "
        f"codes ({catalog_skipped} record(s) skipped: missing/non-9-digit rep code)"
    )

    rows, ssl_mode = asyncio.run(_fetch_rows(creds, args.limit))
    print(f"  fetched {len(rows)} rows (ssl={ssl_mode})")

    profiles: dict[str, dict[str, Any]] = {}
    prefix_map: dict[str, str] = {}
    skipped_invalid_incs = 0
    users_with_events = 0
    total_owned_families = 0
    total_anchor_skus = 0
    total_events = 0
    all_seen_codes: set[str] = set()
    all_matched_codes: set[str] = set()
    all_dropped_codes: set[str] = set()
    all_invalid_codes: set[str] = set()
    family_counts: list[int] = []

    for row in rows:
        user_id = register_pseudonym(row["incs_no"], prefix_map)
        if user_id is None:
            skipped_invalid_incs += 1
            continue
        raw = {col: parse_column(col, row[col]) for col in profile_columns}
        profile, stats = build_profile_record(user_id, raw, rep_index, normalize_profile)
        profiles[user_id] = profile

        all_seen_codes.update(stats["total_codes"])
        all_matched_codes.update(stats["matched_families"])
        all_dropped_codes.update(stats["dropped_codes"])
        all_invalid_codes.update(stats["invalid_codes"])
        n_fam = len(stats["matched_families"])
        if n_fam:
            users_with_events += 1
            total_owned_families += n_fam
            total_anchor_skus += len(stats["anchor_skus"])
            total_events += len(profile.get("purchase_events", []))
            family_counts.append(n_fam)

    ordered = {uid: profiles[uid] for uid in sorted(profiles)}
    write_output_atomic(
        output_path,
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n",
    )

    seen = len(all_seen_codes)
    matched = len(all_matched_codes)

    def _dist(values: list[int]) -> dict[str, Any]:
        if not values:
            return {"min": 0, "max": 0, "mean": 0.0}
        return {"min": min(values), "max": max(values), "mean": round(sum(values) / len(values), 2)}

    # AGGREGATES ONLY — no row-level user/product combinations on stdout.
    summary = {
        "limit": args.limit,
        "ssl_mode": ssl_mode,
        "rows_fetched": len(rows),
        "rows_skipped_invalid_incs": skipped_invalid_incs,
        "users_written": len(ordered),
        "users_with_owned_edges": users_with_events,
        "total_owned_families": total_owned_families,
        "total_anchor_skus": total_anchor_skus,
        "total_purchase_events": total_events,
        "distinct_codes_seen": seen,
        "distinct_codes_matched": matched,
        "distinct_codes_dropped_unmatched": len(all_dropped_codes),
        "distinct_codes_dropped_invalid_format": len(all_invalid_codes),
        "code_match_rate": round(matched / seen, 4) if seen else 0.0,
        "owned_families_per_user": _dist(family_counts),
    }
    print("\n=== BACKFILL STATS (aggregates only) ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nWritten {len(ordered)} profiles → {output_path} (mode 0600)")


if __name__ == "__main__":
    main()
