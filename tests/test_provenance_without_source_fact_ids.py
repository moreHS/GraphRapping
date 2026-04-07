"""Tests: provenance chain works without source_fact_ids (signal_evidence is SoT)."""
import inspect

from src.rec.explainer import explain, ExplanationPath
from src.rec.scorer import ScoredProduct


def test_explanation_works_without_source_fact_ids():
    """Explainer must produce valid output using overlap concepts, not source_fact_ids."""
    scored = ScoredProduct(
        product_id="P001",
        raw_score=0.5,
        shrinked_score=0.4,
        final_score=0.4,
        feature_contributions={"keyword_match": 0.2, "concern_fit": 0.1},
        support_count=10,
    )
    overlap = ["keyword:concept:Keyword:GelLike", "concern:concept:Concern:acne"]
    result = explain(scored, overlap, top_n=5)
    assert result.product_id == "P001"
    assert len(result.paths) > 0, "Should produce explanation paths from overlap concepts"


def test_explanation_paths_use_feature_contributions():
    """Explanation paths must derive from feature_contributions, not source_fact_ids."""
    scored = ScoredProduct(
        product_id="P002",
        raw_score=0.3,
        shrinked_score=0.2,
        final_score=0.2,
        feature_contributions={"brand_match_conf_weighted": 0.07},
        support_count=5,
    )
    overlap = ["brand:concept:Brand:TestBrand"]
    result = explain(scored, overlap, top_n=5)
    for path in result.paths:
        assert isinstance(path, ExplanationPath)
        assert path.contribution > 0


def test_aggregate_has_no_source_fact_ids_dependency():
    """Verify aggregate_product_signals has no runtime dependency on source_fact_ids."""
    import src.mart.aggregate_product_signals as agg_mod
    source = inspect.getsource(agg_mod)
    lines = source.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        assert "source_fact_ids" not in line, \
            f"aggregate_product_signals line {i} references source_fact_ids: {line.strip()}"
