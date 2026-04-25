import inspect

from src.db.repos import signal_repo


def test_signal_repo_writes_promotion_metadata_fields():
    src = inspect.getsource(signal_repo.replace_signals_for_review)

    for field in (
        "evidence_kind",
        "fact_status",
        "source_confidence",
        "target_linked",
        "attribution_source",
    ):
        assert field in src
