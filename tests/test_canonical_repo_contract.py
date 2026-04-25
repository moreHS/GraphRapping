import inspect

from src.db.repos import canonical_repo


def test_canonical_fact_repo_writes_fact_metadata():
    src = "\n".join(
        inspect.getsource(fn)
        for fn in (
            canonical_repo._insert_fact,
            canonical_repo._refresh_fact,
            canonical_repo._reactivate_fact,
        )
    )

    for field in (
        "negated",
        "intensity",
        "evidence_kind",
        "fact_status",
        "target_linked",
        "attribution_source",
    ):
        assert field in src


def test_fact_provenance_repo_writes_generic_source_fields():
    src = inspect.getsource(canonical_repo._replace_provenance)

    assert "source_domain" in src
    assert "source_kind" in src
