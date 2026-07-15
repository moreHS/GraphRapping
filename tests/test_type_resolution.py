"""P7-2 C1: NLP type resolution for mistyped Concern/Goal surface forms.

Covers src/kg/adapter.py::_resolve_mistyped_concept / _lookup_concern_concept_id
/ _lookup_goal_token / _register_resolved_entity, and the end-to-end effect on
kg_result_to_facts: a review relation whose object was mistyped by NER as a
generic type (e.g. Category) but whose surface form is a registered concern/
goal should now clear the predicate-contract gate instead of being rejected
into quarantine_invalid_fact, while an unregistered surface form is rejected
exactly as before (no regression), and an already-correctly-typed entity is
never re-examined against the dictionary (no risk of a spurious rewrite).
"""

from __future__ import annotations

from src.canonical.canonical_fact_builder import CanonicalFactBuilder
from src.common.config_loader import load_predicate_contracts
from src.common.ids import make_concept_iri
from src.kg.adapter import (
    _lookup_concern_concept_id,
    _lookup_goal_token,
    _resolve_mistyped_concept,
    kg_result_to_facts,
)
from src.kg.models import KGEdge, KGEntity, KGResult


def _contracts() -> dict:
    return load_predicate_contracts()


# ---------------------------------------------------------------------------
# Unit tests: _resolve_mistyped_concept / dict lookups
# ---------------------------------------------------------------------------


def test_lookup_concern_concept_id_exact_membership_only() -> None:
    """Dict membership is the sole gate — no substring/heuristic matching.

    '속건조' is a real quarantine-observed surface form that CONTAINS '건조'
    (a dict key) but is not itself a key. It must not resolve — otherwise a
    bare-normalization/substring judgment could silently reclassify unrelated
    words that merely share a substring with a curated concern.
    """
    assert _lookup_concern_concept_id("건조") == "concern_dryness"
    assert _lookup_concern_concept_id("속건조") is None
    assert _lookup_concern_concept_id("") is None


def test_lookup_goal_token_exact_membership_only() -> None:
    assert _lookup_goal_token("톤업") == "톤업"
    assert _lookup_goal_token("톤업도") is None  # observed quarantine surface form, not a key


def test_resolve_mistyped_concept_promotes_category_to_concern_for_affects_object() -> None:
    resolved = _resolve_mistyped_concept("affects", "object", "Category", "건조")
    assert resolved == ("Concern", "concern_dryness")


def test_resolve_mistyped_concept_returns_none_for_unregistered_word() -> None:
    """No dictionary hit → no resolution; the fact stays rejected exactly as before."""
    assert _resolve_mistyped_concept("affects", "object", "Category", "아무말") is None


def test_resolve_mistyped_concept_never_touches_an_already_valid_type() -> None:
    """An entity already typed within the contract's allowed set is left alone,
    even if its word happens to also be a dictionary member elsewhere — this
    is what makes the resolver purely additive (can only rescue a rejection,
    never alter an already-accepted fact)."""
    assert _resolve_mistyped_concept("affects", "object", "Concern", "건조") is None


def test_resolve_mistyped_concept_respects_predicate_contract_scope() -> None:
    """`affects` only allows Concern as object (not Goal) — a Goal-dictionary
    word must NOT be promoted here, even though the word itself is valid
    elsewhere (e.g. under `used_for`, which allows Concern|Goal)."""
    assert _resolve_mistyped_concept("affects", "object", "Category", "톤업") is None


def test_resolve_mistyped_concept_supports_goal_where_contract_allows_it() -> None:
    contract = _contracts()["used_for"]
    assert "Goal" in contract["allowed_object_types"].split("|")
    resolved = _resolve_mistyped_concept("used_for", "object", "Category", "톤업")
    assert resolved == ("Goal", "톤업")


def test_resolve_mistyped_concept_supports_subject_side_for_reverse_predicates() -> None:
    """`affected_by`/`caused_by` carry Concern on the SUBJECT side."""
    contract = _contracts()["affected_by"]
    assert contract["allowed_subject_types"] == "Concern"
    resolved = _resolve_mistyped_concept("affected_by", "subject", "Category", "여드름")
    assert resolved == ("Concern", "concern_acne")


def test_resolve_mistyped_concept_unknown_predicate_is_noop() -> None:
    assert _resolve_mistyped_concept("no_such_predicate", "object", "Category", "건조") is None


# ---------------------------------------------------------------------------
# End-to-end: kg_result_to_facts clears the predicate-contract gate
# ---------------------------------------------------------------------------


def _kg_result_affects_concern(*, obj_word: str, obj_type: str = "CAT") -> KGResult:
    entities = [
        KGEntity(entity_id="prd", entity_type="PRD", normalized_value="prd1", word="상품"),
        KGEntity(entity_id="obj", entity_type=obj_type, normalized_value=obj_word, word=obj_word),
    ]
    result = KGResult(
        entities=entities,
        edges=[
            KGEdge(
                edge_id="edge_affects",
                subj_entity_id="prd",
                obj_entity_id="obj",
                relation_type="AFFECTS",
                sentiment="POS",
            )
        ],
    )
    result.entity_map = {e.entity_id: e for e in entities}
    return result


def test_kg_on_mistyped_concern_object_clears_contract_and_preserves_provenance() -> None:
    builder = CanonicalFactBuilder(predicate_contracts=_contracts())

    stats = kg_result_to_facts(
        kg_result=_kg_result_affects_concern(obj_word="건조"),
        review_id="rv_concern",
        target_product_iri="concept:Product:prd1",
        builder=builder,
    )

    assert builder.invalid_facts == []  # contract violation avoided
    assert stats["dropped"] == 0
    [fact] = builder.facts
    assert fact.predicate == "affects"
    assert fact.subject_type == "Product"
    assert fact.object_type == "Concern"
    assert fact.object_iri == make_concept_iri("Concern", "concern_dryness")

    # Provenance: original (mistyped) type preserved as an audit qualifier —
    # retyping must never be a silent overwrite.
    qualifier_keys = {q.qualifier_key: q for q in fact.qualifiers}
    assert "type_resolved_from_object" in qualifier_keys
    assert qualifier_keys["type_resolved_from_object"].qualifier_value_text == "Category"

    # The corrected entity is registered under the corrected IRI/type — a
    # downstream consumer resolving fact.object_iri finds a Concern entity,
    # not a leftover Category registration.
    concern_entities = [e for e in builder.entities if e.entity_iri == fact.object_iri]
    assert len(concern_entities) == 1
    assert concern_entities[0].entity_type == "Concern"


def test_kg_on_unregistered_concern_word_still_rejected_no_regression() -> None:
    """Without a dictionary hit, behavior is byte-identical to pre-C1: the
    fact is rejected by the predicate contract and lands in invalid_facts."""
    builder = CanonicalFactBuilder(predicate_contracts=_contracts())

    kg_result_to_facts(
        kg_result=_kg_result_affects_concern(obj_word="전혀등록안된단어"),
        review_id="rv_unregistered",
        target_product_iri="concept:Product:prd1",
        builder=builder,
    )

    assert builder.facts == []
    [invalid] = builder.invalid_facts
    assert invalid["predicate"] == "affects"
    assert invalid["object_type"] == "Category"


def test_kg_on_used_for_goal_resolves_but_stays_out_of_projection() -> None:
    """`used_for` allows Goal on the contract, so a Goal-dict word now clears
    the contract gate — but there is currently no SignalFamily/projection_registry
    row for (Product, Goal), so this fact still cannot reach a live serving
    signal. This test documents that boundary precisely rather than
    overclaiming a live Goal signal (see the C1 completion report note)."""
    builder = CanonicalFactBuilder(predicate_contracts=_contracts())
    entities = [
        KGEntity(entity_id="prd", entity_type="PRD", normalized_value="prd1", word="상품"),
        KGEntity(entity_id="obj", entity_type="CAT", normalized_value="톤업", word="톤업"),
    ]
    result = KGResult(entities=entities, edges=[
        KGEdge(edge_id="e1", subj_entity_id="prd", obj_entity_id="obj", relation_type="USED_FOR"),
    ])
    result.entity_map = {e.entity_id: e for e in entities}

    kg_result_to_facts(
        kg_result=result, review_id="rv_goal", target_product_iri="concept:Product:prd1", builder=builder,
    )

    assert builder.invalid_facts == []  # contract cleared
    [fact] = builder.facts
    assert fact.object_type == "Goal"
    assert fact.object_iri == make_concept_iri("Goal", "톤업")
