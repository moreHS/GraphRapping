"""
P1-5 (Wave 3.5): `verify_user_id_stability` was a dead preflight gate with no
operational caller. Activation required an async DB pool that `FullLoadConfig`
doesn't expose, so the function is removed rather than kept as a future-use
marker. This test pins the removal so it can't silently come back.

If/when personal-agent DB integration lands and a real pool is wired in, a
new preflight check can be designed against the actual plumbing then.
"""

from __future__ import annotations

import inspect

from src.loaders import user_loader


def test_verify_user_id_stability_not_in_user_loader() -> None:
    assert not hasattr(user_loader, "verify_user_id_stability"), (
        "verify_user_id_stability re-introduced without an operational caller. "
        "Design against a real pool contract; do not restore as dead preflight."
    )


def test_no_lingering_callers_in_codebase() -> None:
    """No callable references to the removed symbol may survive.

    The module docstring in `src/loaders/user_loader.py` legitimately records
    the removal reason (P1-5 note) — that's documentation, not a caller.
    Anything else (function def, import, call) is a regression.
    """
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    SYMBOL = "verify_user_id_stability"
    offenders: list[str] = []

    def _references_call(path: Path) -> bool:
        text = path.read_text(encoding="utf-8")
        if SYMBOL not in text:
            return False
        # Strip the module docstring (first triple-quoted block) and re-check.
        # Heuristic: if symbol survives the docstring removal, it's a real ref.
        if text.lstrip().startswith('"""'):
            end = text.find('"""', text.find('"""') + 3)
            if end != -1:
                body = text[end + 3:]
                return SYMBOL in body
        return True

    for path in (repo_root / "src").rglob("*.py"):
        if _references_call(path):
            offenders.append(str(path.relative_to(repo_root)))
    for path in (repo_root / "tests").rglob("*.py"):
        if path.name == "test_verify_user_id_stability_removed.py":
            continue
        if _references_call(path):
            offenders.append(str(path.relative_to(repo_root)))
    assert not offenders, f"Lingering references to removed symbol: {offenders}"


def test_user_loader_module_docstring_documents_removal() -> None:
    """The module docstring records why the function was removed."""
    doc = inspect.getmodule(user_loader).__doc__ or ""
    assert "P1-5" in doc, "P1-5 removal note missing from user_loader module docstring"
    assert "verify_user_id_stability" in doc
