"""Minimal ``.env`` loader for the input connectors (IC-3 / plan 2026-07-20).

GraphRapping keeps every DB-related environment variable in one git-ignored
``.env`` file at the repo root (the committed ``.env.example`` documents the
keys). Loading is **explicit opt-in**: the connector scripts call
:func:`load_env_file` at the top of ``main()`` — importing a module never has the
side effect of reading a file, so the demo/test environment is never polluted.

Design (plan §2 "env 일원화"):
* No new dependency — python-dotenv is NOT added; this ~30-line parser is enough.
* ``override=False`` (default): a variable already present in the environment
  (shell / CI / an earlier loader) is left untouched, so ``.env`` never fights an
  explicit shell export. ``override=True`` lets ``.env`` win.
* Tests inject an explicit ``environ`` mapping, so a test never mutates the real
  ``os.environ`` and ambient shell variables never leak into a run.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


def _strip_value(raw: str) -> str:
    """Trim surrounding whitespace, then a single matching quote pair."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def parse_env_text(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines into a dict (pure — no environment side effect).

    Blank lines and ``#`` comments are ignored, an optional leading ``export`` is
    stripped (common in hand-written ``.env`` files), the split is on the first
    ``=`` only, and surrounding quotes on the value are removed. Lines without an
    ``=`` are skipped rather than raising, so a partially-formatted file is
    tolerated.
    """
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export ") or stripped.startswith("export\t"):
            stripped = stripped[len("export "):].lstrip()
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            continue
        parsed[key] = _strip_value(value)
    return parsed


def load_env_file(
    path: str | Path = ".env",
    *,
    override: bool = False,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Load a ``.env`` file into the environment (opt-in) and return its contents.

    Parses ``path`` with :func:`parse_env_text` and merges the result into
    ``environ`` (``os.environ`` by default). With ``override=False`` (the default)
    a key already present in ``environ`` is preserved — the shell / CI always wins
    over ``.env`` — so this is safe to call unconditionally at startup. A missing
    file is harmless: nothing is applied and an empty dict is returned.

    Returns the full parsed ``{KEY: VALUE}`` from the file (regardless of whether
    each key was applied), which callers/tests can inspect.
    """
    target = environ if environ is not None else os.environ
    env_path = Path(path)
    if not env_path.exists():
        return {}
    parsed = parse_env_text(env_path.read_text(encoding="utf-8"))
    for key, value in parsed.items():
        if override or key not in target:
            target[key] = value
    return parsed
