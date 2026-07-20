"""IC-3 .env loader (plan 2026-07-20 §1).

Parsing (comments / quotes / whitespace / export / first-`=` split) and the
opt-in merge semantics: override=False preserves an already-set variable
(shell/CI wins), override=True lets the file win, a missing file is harmless,
and the full parsed dict is always returned. Tests inject an explicit ``environ``
mapping so the real ``os.environ`` is never mutated.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.common.env_file import load_env_file, parse_env_text


# ---------------------------------------------------------------------------
# parse_env_text (pure)
# ---------------------------------------------------------------------------

def test_parse_basic_key_value() -> None:
    assert parse_env_text("A=1\nB=two\n") == {"A": "1", "B": "two"}


def test_parse_ignores_comments_and_blank_lines() -> None:
    assert parse_env_text("# comment\n\n   \nA=1\n#B=2\n") == {"A": "1"}


def test_parse_strips_quotes_and_whitespace() -> None:
    parsed = parse_env_text('A = "quoted value" \nB=\'single\'\nC=  bare  \n')
    assert parsed == {"A": "quoted value", "B": "single", "C": "bare"}


def test_parse_strips_export_prefix() -> None:
    assert parse_env_text("export A=1\nexport\tB=2\n") == {"A": "1", "B": "2"}


def test_parse_splits_on_first_equals_only() -> None:
    assert parse_env_text("URL=postgres://u:p@host/db?x=1\n") == {
        "URL": "postgres://u:p@host/db?x=1"
    }


def test_parse_skips_lines_without_equals() -> None:
    assert parse_env_text("NOTHING\nA=1\n") == {"A": "1"}


# ---------------------------------------------------------------------------
# load_env_file (opt-in merge)
# ---------------------------------------------------------------------------

def test_load_missing_file_is_harmless(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    assert load_env_file(tmp_path / "nope.env", environ=environ) == {}
    assert environ == {}


def test_load_override_false_preserves_existing(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("A=fromfile\nB=fromfile\n", encoding="utf-8")
    environ = {"A": "fromshell"}
    parsed = load_env_file(env_path, environ=environ)
    assert parsed == {"A": "fromfile", "B": "fromfile"}  # full parse returned
    assert environ["A"] == "fromshell"  # existing shell value preserved
    assert environ["B"] == "fromfile"   # gap filled from file


def test_load_override_true_replaces_existing(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("A=fromfile\n", encoding="utf-8")
    environ = {"A": "fromshell"}
    load_env_file(env_path, override=True, environ=environ)
    assert environ["A"] == "fromfile"


def test_load_defaults_to_os_environ_without_leak(
    monkeypatch, tmp_path: Path
) -> None:
    # monkeypatch owns the key (auto-reverted); override=False must NOT touch it,
    # so no NEW key is written into the real os.environ.
    env_path = tmp_path / ".env"
    env_path.write_text("GRAPHRAPPING_ENVFILE_PROBE=fromfile\n", encoding="utf-8")
    monkeypatch.setenv("GRAPHRAPPING_ENVFILE_PROBE", "fromshell")
    load_env_file(env_path)  # environ defaults to os.environ
    assert os.environ["GRAPHRAPPING_ENVFILE_PROBE"] == "fromshell"
