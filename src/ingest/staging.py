"""Shared staging helpers for input connectors (IC-0 / plan 2026-07-19 §2).

Confines connector output to the git-ignored ``mockdata/real/{users,reviews,
products}/`` tree with the same protections the user-profile backfill script
established — 0700 directories, 0600 atomic (tmp→rename) writes, and symlink
refusal — extracted here so every connector reuses ONE implementation (plan:
no duplicate impl). ``scripts/fetch_user_profiles_pg.write_output_atomic`` now
delegates to :func:`write_json_atomic`.

Determinism: the snapshot date is INJECTED by the caller (never ``date.now``),
so tests are reproducible and manifests are stable.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_DATA_DIR = _PROJECT_ROOT / "mockdata" / "real"

# Staging subdirectory per source kind.
STAGING_SUBDIRS: dict[str, str] = {
    "users": "users",
    "reviews": "reviews",
    "products": "products",
}

_YYYYMMDD_RE = re.compile(r"^\d{8}$")


# ---------------------------------------------------------------------------
# Directory + filename conventions
# ---------------------------------------------------------------------------

def staging_dir(kind: str, real_dir: Path = REAL_DATA_DIR) -> Path:
    """Return the staging subdirectory path for ``kind`` (not created)."""
    if kind not in STAGING_SUBDIRS:
        raise ValueError(
            f"unknown staging kind: {kind!r} (expected one of {sorted(STAGING_SUBDIRS)})"
        )
    return real_dir / STAGING_SUBDIRS[kind]


def ensure_staging_dir(kind: str, real_dir: Path = REAL_DATA_DIR) -> Path:
    """Create (0700) and return the staging subdirectory for ``kind``.

    The parent ``real_dir`` is also forced to 0700 so a connector never widens
    the git-ignored real-data tree's permissions.
    """
    target = staging_dir(kind, real_dir)
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(real_dir, 0o700)
    os.chmod(target, 0o700)
    return target


def snapshot_filename(name: str, date_str: str, ext: str = "json") -> str:
    """Return ``{name}_{YYYYMMDD}.{ext}``. ``date_str`` is caller-injected."""
    if not _YYYYMMDD_RE.match(date_str):
        raise ValueError(f"date_str must be YYYYMMDD, got {date_str!r}")
    return f"{name}_{date_str}.{ext}"


# ---------------------------------------------------------------------------
# Path guard (one level of subdirectory allowed; symlink-safe)
# ---------------------------------------------------------------------------

def validate_staging_path(output: Path, real_dir: Path = REAL_DATA_DIR) -> Path:
    """Confine ``output`` to ``real_dir`` directly OR one subdirectory deep.

    Generalizes ``fetch_user_profiles_pg.validate_output_path`` (which allows
    only files directly inside ``real_dir``) to also accept
    ``real_dir/<subdir>/file`` for the users/reviews/products layout, keeping the
    same protections: a symlinked ``real_dir`` is rejected, a symlinked output
    file is rejected, and ``..`` traversal / deeper nesting / symlinked
    intermediates that escape ``real_dir`` are rejected because the resolved
    parent would no longer sit under ``real_dir``.
    """
    if real_dir.exists() and real_dir.is_symlink():
        raise ValueError(f"real-data dir must not be a symlink: {real_dir}")
    candidate = output if output.is_absolute() else Path.cwd() / output
    if candidate.is_symlink():
        raise ValueError(f"staging output must not be a symlink: {output}")
    resolved = candidate.resolve()
    real_resolved = real_dir.resolve()
    parent = resolved.parent
    if parent == real_resolved:
        return resolved
    if parent.parent == real_resolved and parent != parent.parent:
        return resolved
    raise ValueError(
        f"staging output must be inside {real_dir} (git-ignored real-data dir), "
        f"at most one subdirectory deep; got: {output}"
    )


# ---------------------------------------------------------------------------
# Atomic write (0600 file, 0700 dir)
# ---------------------------------------------------------------------------

def write_json_atomic(path: Path, payload: str, *, tmp_prefix: str = ".tmp_staging_") -> None:
    """Atomic (tmp→rename) write with file mode 0600 and dir mode 0700.

    Canonical implementation shared by every connector (the user-profile
    backfill script's ``write_output_atomic`` delegates here).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=tmp_prefix, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(tmp_name, 0o600)  # mkstemp default is already 0600; explicit for clarity
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

@dataclass
class StagingManifest:
    """Aggregate-only manifest recorded alongside a staged snapshot.

    Records the snapshot path/format/count, an add/update/unchanged/conflict
    delta (all default 0 when a connector does not compute one), the injected
    ``generated_at``, and a compact validation summary. NEVER holds record
    payload (only aggregate counts / violation keys).
    """

    path: str
    format: str
    count: int
    generated_at: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    conflict: int = 0
    validation: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "format": self.format,
            "count": self.count,
            "generated_at": self.generated_at,
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "conflict": self.conflict,
            "validation": self.validation,
        }
        data.update(self.extra)
        return data


def write_manifest(manifest_path: Path, manifest: StagingManifest) -> None:
    """Write ``manifest.json`` atomically (0600) next to a staged snapshot."""
    write_json_atomic(
        manifest_path,
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        tmp_prefix=".tmp_manifest_",
    )
