"""
Phase 4.3: ontology integration validator tests
(src/kg/ontology_validator.py, fable_doc/03_improvement_plan.md).

Two groups:

- A CI gate test that runs the validator against the real configs/ files and
  asserts zero violations. If this starts failing, a real cross-config
  ontology inconsistency was introduced (or the validator's own "entity type
  universe" — see the module docstring's "Design note" — needs updating for
  a legitimate new concept-plane type).
- One detection test per documented check ((a)-(e), including each of check
  (c)'s 4 distinct failure modes), each injecting a single synthetic
  violation into an otherwise-clean in-memory fixture and asserting the
  validator's structured output (`rule`/`file`/`item`/`reason`) catches it.
  Fixtures never touch configs/ on disk — `validate_ontology` is a pure
  function over already-loaded dict/list structures.
- v2 (Phase 7 A5) checks (f)-(i): same two-layer pattern — injection tests
  prove each detector fires, current-state tests pin what the real configs/
  code report today (meta count consistent, exactly the 4 known orphan types
  Color/Volume/AgeBand/Event as warnings, real bridge constants clean). The
  (h) liveness pipeline run is NOT exercised here (it executes the full
  in-memory pipeline — covered by the pure `build_liveness_report` diff test
  instead; the runner backs the manual `validate-ontology --liveness` CLI).
"""

from __future__ import annotations

from typing import Any

from src.kg.ontology_validator import (
    _load_bridge_constants,
    build_liveness_report,
    check_bridge_constant_coverage,
    check_canonical_map_meta,
    check_orphan_entity_types,
    collect_ontology_warnings,
    validate_current_ontology_configs,
    validate_ontology,
)


# ---------------------------------------------------------------------------
# Synthetic fixture builders (mirrors tests/test_personalization_contract_checklist.py's
# **overrides builder style)
# ---------------------------------------------------------------------------


def _entity_types(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "types": [
            {"code": "PRD", "neo4j_label": "PRD"},
            {"code": "BRD", "neo4j_label": "BRD"},
            {"code": "지속력", "neo4j_label": "BEE_ATTR", "is_bee": True},
        ],
        "type_aliases": {},
    }
    base.update(overrides)
    return base


def _relation_types(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "types": [
            {"code": "has_attribute", "neo4j_type": "HAS_ATTRIBUTE"},
            {"code": "brand_of", "neo4j_type": "BRAND_OF"},
            {"code": "used_for", "neo4j_type": "USED_FOR"},
        ],
        "special_edges": {},
    }
    base.update(overrides)
    return base


def _contract_row(**overrides: Any) -> dict[str, str]:
    base = {
        "predicate": "has_attribute",
        "allowed_subject_types": "Product",
        "allowed_object_types": "BEEAttr",
        "object_ref_kind": "CONCEPT",
        "polarity_allowed": "POS|NEG|NEU",
        "inverse_predicate": "",
        "qualifier_allowed": "Y",
        "projectable_to_layer3": "Y",
    }
    base.update(overrides)
    return base


def _projection_row(**overrides: Any) -> dict[str, str]:
    base = {
        "registry_version": "v1",
        "input_predicate": "has_attribute",
        "subject_type": "Product",
        "object_type": "BEEAttr",
        "polarity": "",
        "qualifier_required": "N",
        "qualifier_type": "",
        "output_signal_family": "BEE_ATTR",
        "output_edge_type": "HAS_BEE_ATTR_SIGNAL",
        "output_dst_type": "BEEAttr",
        "output_transform": "identity",
        "output_weight_rule": "bee_weight",
        "if_unresolved_action": "",
        "notes": "",
        "allowed_evidence_kind": "",
        "min_confidence": "",
        "promotion_mode": "",
    }
    base.update(overrides)
    return base


def _valid_contracts() -> list[dict[str, str]]:
    return [
        _contract_row(),  # has_attribute: Product -> BEEAttr, projectable
        _contract_row(
            predicate="brand_of",
            allowed_subject_types="Product",
            allowed_object_types="Brand",
            projectable_to_layer3="N",
        ),
    ]


def _valid_projections() -> list[dict[str, str]]:
    return [
        _projection_row(),  # has_attribute: active projection, matches contract row above
        _projection_row(
            input_predicate="brand_of",
            subject_type="Product",
            object_type="Brand",
            output_signal_family="",
            output_edge_type="",
            output_dst_type="",
            output_transform="",
            output_weight_rule="",
            if_unresolved_action="KEEP_CANONICAL_ONLY",
        ),  # administrative row: no active projection, consistent with contract's "N"
    ]


# ---------------------------------------------------------------------------
# CI gate: real configs/ must currently be clean
# ---------------------------------------------------------------------------


def test_current_configs_have_no_violations() -> None:
    violations = validate_current_ontology_configs()
    assert violations == [], f"unexpected ontology violations in configs/: {violations}"


def test_clean_synthetic_fixture_has_no_violations() -> None:
    """Sanity check on the fixture builders themselves, independent of configs/ drift."""
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), _valid_projections())
    assert violations == []


# ---------------------------------------------------------------------------
# (a) predicate_contracts.csv predicate must exist in kg_relation_types.json
# ---------------------------------------------------------------------------


def test_detects_predicate_missing_from_relation_types() -> None:
    contracts = _valid_contracts() + [
        _contract_row(
            predicate="totally_made_up_predicate",
            allowed_subject_types="Product",
            allowed_object_types="Brand",
        ),
    ]
    violations = validate_ontology(_entity_types(), _relation_types(), contracts, _valid_projections())
    matches = [v for v in violations if v.rule == "predicate_in_relation_types"]
    assert len(matches) == 1
    assert matches[0].file == "predicate_contracts.csv"
    assert matches[0].item == "totally_made_up_predicate"


# ---------------------------------------------------------------------------
# (b) predicate_contracts.csv subject/object types must exist in the entity
#     type universe derived from kg_entity_types.json
# ---------------------------------------------------------------------------


def test_detects_contract_type_missing_from_entity_universe() -> None:
    contracts = _valid_contracts() + [
        _contract_row(
            predicate="used_for",
            allowed_subject_types="Product",
            allowed_object_types="TotallyBogusType",
            projectable_to_layer3="N",
        ),
    ]
    violations = validate_ontology(_entity_types(), _relation_types(), contracts, _valid_projections())
    matches = [v for v in violations if v.rule == "contract_type_in_entity_universe"]
    assert len(matches) == 1
    assert "TotallyBogusType" in matches[0].item


# ---------------------------------------------------------------------------
# (c) projection_registry.csv input_predicate existence + projectability
# ---------------------------------------------------------------------------


def test_detects_projection_predicate_missing_from_relation_types() -> None:
    projections = _valid_projections() + [_projection_row(input_predicate="another_bogus_predicate")]
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), projections)
    matches = [v for v in violations if v.rule == "projection_predicate_in_relation_types"]
    assert len(matches) == 1


def test_detects_projection_with_no_matching_contract() -> None:
    """input_predicate exists in kg_relation_types.json but has no predicate_contracts.csv row."""
    projections = [
        _projection_row(
            input_predicate="used_for",
            subject_type="Product",
            object_type="Concern",
            output_signal_family="CONCERN_POS",
            output_edge_type="ADDRESSES_CONCERN_SIGNAL",
            output_dst_type="Concern",
        ),
    ]
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), projections)
    matches = [v for v in violations if v.rule == "projection_contract_exists"]
    assert len(matches) == 1


def test_detects_projection_not_projectable_per_contract() -> None:
    """An active projection exists for a predicate whose contract forbids projection."""
    projections = [
        _projection_row(),  # has_attribute — clean, active, matches contract
        _projection_row(
            input_predicate="brand_of",
            subject_type="Product",
            object_type="Brand",
            output_signal_family="CATALOG_VALIDATION",
            output_edge_type="CATALOG_VALIDATION_SIGNAL",
            output_dst_type="Brand",
        ),  # brand_of contract has projectable_to_layer3="N" in _valid_contracts()
    ]
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), projections)
    matches = [v for v in violations if v.rule == "projection_projectable_to_layer3"]
    assert len(matches) == 1
    assert "brand_of" in matches[0].item


def test_detects_projection_subject_type_not_in_contract() -> None:
    projections = [_projection_row(subject_type="Brand")]  # has_attribute only allows Product as subject
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), projections)
    matches = [v for v in violations if v.rule == "projection_subject_type_in_contract"]
    assert len(matches) == 1


def test_detects_projection_object_type_not_in_contract() -> None:
    projections = [_projection_row(object_type="Brand")]  # has_attribute only allows BEEAttr as object
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), projections)
    matches = [v for v in violations if v.rule == "projection_object_type_in_contract"]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# (d) projection_registry.csv output_dst_type must exist in the entity type universe
# ---------------------------------------------------------------------------


def test_detects_projection_dst_type_missing_from_entity_universe() -> None:
    projections = [_projection_row(output_dst_type="NotARealType")]
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), projections)
    matches = [v for v in violations if v.rule == "projection_dst_type_in_entity_universe"]
    assert len(matches) == 1
    assert "NotARealType" in matches[0].item


# ---------------------------------------------------------------------------
# (e) duplicate definitions
# ---------------------------------------------------------------------------


def test_detects_duplicate_entity_type_code() -> None:
    entity_types = _entity_types(types=[
        {"code": "PRD", "neo4j_label": "PRD"},
        {"code": "PRD", "neo4j_label": "PRD"},
        {"code": "BRD", "neo4j_label": "BRD"},
        {"code": "지속력", "neo4j_label": "BEE_ATTR", "is_bee": True},
    ])
    violations = validate_ontology(entity_types, _relation_types(), _valid_contracts(), _valid_projections())
    matches = [v for v in violations if v.rule == "duplicate_entity_type_code"]
    assert len(matches) == 1
    assert matches[0].item == "PRD"


def test_detects_duplicate_relation_code() -> None:
    relation_types = _relation_types(types=[
        {"code": "has_attribute", "neo4j_type": "HAS_ATTRIBUTE"},
        {"code": "has_attribute", "neo4j_type": "HAS_ATTRIBUTE"},
        {"code": "brand_of", "neo4j_type": "BRAND_OF"},
        {"code": "used_for", "neo4j_type": "USED_FOR"},
    ])
    violations = validate_ontology(_entity_types(), relation_types, _valid_contracts(), _valid_projections())
    matches = [v for v in violations if v.rule == "duplicate_relation_code"]
    assert len(matches) == 1
    assert matches[0].item == "has_attribute"


def test_detects_duplicate_relation_code_case_insensitive() -> None:
    """Two casings of the same predicate code are the same predicate for dedup
    purposes — mirrors _relation_universe's case-insensitive existence check
    used by checks (a)/(c), so this can't slip past as two distinct codes."""
    relation_types = _relation_types(types=[
        {"code": "has_attribute", "neo4j_type": "HAS_ATTRIBUTE"},
        {"code": "HAS_ATTRIBUTE", "neo4j_type": "HAS_ATTRIBUTE"},
        {"code": "brand_of", "neo4j_type": "BRAND_OF"},
        {"code": "used_for", "neo4j_type": "USED_FOR"},
    ])
    violations = validate_ontology(_entity_types(), relation_types, _valid_contracts(), _valid_projections())
    matches = [v for v in violations if v.rule == "duplicate_relation_code"]
    assert len(matches) == 1
    assert matches[0].item == "has_attribute"


def test_detects_duplicate_contract_predicate() -> None:
    contracts = _valid_contracts() + [_contract_row()]  # second has_attribute row
    violations = validate_ontology(_entity_types(), _relation_types(), contracts, _valid_projections())
    matches = [v for v in violations if v.rule == "duplicate_contract_predicate"]
    assert len(matches) == 1
    assert matches[0].item == "has_attribute"


def test_detects_duplicate_contract_predicate_case_insensitive() -> None:
    """Same predicate, different casing, in two predicate_contracts.csv rows."""
    contracts = _valid_contracts() + [_contract_row(predicate="HAS_ATTRIBUTE")]
    violations = validate_ontology(_entity_types(), _relation_types(), contracts, _valid_projections())
    matches = [v for v in violations if v.rule == "duplicate_contract_predicate"]
    assert len(matches) == 1
    assert matches[0].item == "has_attribute"


def test_detects_duplicate_projection_row() -> None:
    projections = _valid_projections() + [_projection_row()]  # second has_attribute/Product/BEEAttr/"" row
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), projections)
    matches = [v for v in violations if v.rule == "duplicate_projection_row"]
    assert len(matches) == 1


def test_detects_duplicate_projection_row_case_insensitive() -> None:
    """Same predicate/subject/object/polarity combo, input_predicate differs
    only by casing — still the same projection rule for dedup purposes."""
    projections = _valid_projections() + [_projection_row(input_predicate="HAS_ATTRIBUTE")]
    violations = validate_ontology(_entity_types(), _relation_types(), _valid_contracts(), projections)
    matches = [v for v in violations if v.rule == "duplicate_projection_row"]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# (f) relation_canonical_map.json meta.total_labels vs actual entry count
# ---------------------------------------------------------------------------


def test_detects_canonical_map_meta_count_mismatch() -> None:
    """Injected re-creation of the historical 65-vs-68 drift: the declared
    meta count disagrees with the actual number of label_to_canonical entries."""
    cfg = {
        "label_to_canonical": {"used_by": "used_by", "affects": "affects", "owns": "owns"},
        "meta": {"total_labels": 65},
    }
    violations = check_canonical_map_meta(cfg)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule == "canonical_map_meta_count"
    assert v.file == "relation_canonical_map.json"
    assert v.severity == "error"
    assert "65" in v.item and "3 entries" in v.reason


def test_canonical_map_meta_count_match_is_clean() -> None:
    cfg = {
        "label_to_canonical": {"used_by": "used_by", "affects": "affects"},
        "meta": {"total_labels": 2},
    }
    assert check_canonical_map_meta(cfg) == []


def test_canonical_map_without_meta_count_is_not_flagged() -> None:
    """A file that declares no count makes no claim to verify."""
    assert check_canonical_map_meta({"label_to_canonical": {"a": "a"}}) == []
    assert check_canonical_map_meta({"label_to_canonical": {"a": "a"}, "meta": {}}) == []


# ---------------------------------------------------------------------------
# (g) orphan entity types — raw-extracted but referenced by no contract row
# ---------------------------------------------------------------------------


def test_detects_orphan_entity_type_as_warning() -> None:
    """COL is raw-extracted (node created) but no contract row references
    Color as subject or object -> warning-severity orphan."""
    entity_types = _entity_types(types=[
        {"code": "PRD", "neo4j_label": "PRD"},
        {"code": "BRD", "neo4j_label": "BRD"},
        {"code": "COL", "neo4j_label": "COL"},
        {"code": "지속력", "neo4j_label": "BEE_ATTR", "is_bee": True},
    ])
    violations = check_orphan_entity_types(entity_types, _valid_contracts())
    orphan_items = {v.item for v in violations}
    assert "Color" in orphan_items
    color = next(v for v in violations if v.item == "Color")
    assert color.rule == "orphan_entity_type"
    assert color.severity == "warning"
    assert "COL" in color.reason
    # Product/Brand/BEEAttr are referenced by _valid_contracts() -> not orphans.
    assert {"Product", "Brand", "BEEAttr"} & orphan_items == set()


def test_referenced_type_is_not_an_orphan() -> None:
    """Adding a contract row that references Color clears the orphan warning."""
    entity_types = _entity_types(types=[
        {"code": "PRD", "neo4j_label": "PRD"},
        {"code": "COL", "neo4j_label": "COL"},
        {"code": "지속력", "neo4j_label": "BEE_ATTR", "is_bee": True},
    ])
    contracts = _valid_contracts() + [
        _contract_row(
            predicate="used_for",
            allowed_subject_types="Product",
            allowed_object_types="Color",
            projectable_to_layer3="N",
        ),
    ]
    violations = check_orphan_entity_types(entity_types, contracts)
    assert "Color" not in {v.item for v in violations}


def test_current_configs_orphan_types_are_exactly_the_known_four() -> None:
    """Current-state pin: the real configs surface exactly Color/Volume/
    AgeBand/Event as orphan entity types (fable_doc/06 진단 §3), and every
    static warning is warning-severity (never CI-gating)."""
    warnings = collect_ontology_warnings()
    orphans = sorted(v.item for v in warnings if v.rule == "orphan_entity_type")
    assert orphans == ["AgeBand", "Color", "Event", "Volume"]
    assert all(v.severity == "warning" for v in warnings)


# ---------------------------------------------------------------------------
# (h) vocabulary liveness — pure diff step (pipeline runner not exercised here)
# ---------------------------------------------------------------------------


def test_liveness_report_diffs_defined_vs_generated() -> None:
    report = build_liveness_report(
        fixture="dense_golden",
        kg_mode="on",
        total_signals=10,
        defined_signal_families={"BEE_ATTR", "TOOL", "CONCERN_POS"},
        generated_signal_families={"BEE_ATTR"},
        defined_object_types={"BEEAttr", "Tool"},
        generated_object_types={"BEEAttr"},
    )
    assert report.dead_signal_families == ["CONCERN_POS", "TOOL"]
    assert report.dead_object_types == ["Tool"]

    warnings = report.warnings()
    assert {(v.rule, v.item) for v in warnings} == {
        ("dead_signal_family", "CONCERN_POS"),
        ("dead_signal_family", "TOOL"),
        ("dead_object_type", "Tool"),
    }
    assert all(v.severity == "warning" for v in warnings)
    assert all(v.file == "projection_registry.csv" for v in warnings)


def test_liveness_report_with_full_coverage_has_no_warnings() -> None:
    report = build_liveness_report(
        fixture="dense_golden",
        kg_mode="on",
        total_signals=10,
        defined_signal_families={"BEE_ATTR"},
        generated_signal_families={"BEE_ATTR"},
        defined_object_types={"BEEAttr"},
        generated_object_types={"BEEAttr"},
    )
    assert report.dead_signal_families == []
    assert report.dead_object_types == []
    assert report.warnings() == []


# ---------------------------------------------------------------------------
# (i) 3-layer bridge constant coverage
# ---------------------------------------------------------------------------


def test_detects_bridge_key_missing_from_entity_types() -> None:
    violations = check_bridge_constant_coverage(
        _entity_types(),
        {"_TEST_BRIDGE": {"BOGUS_TAG": "Product"}},
    )
    matches = [v for v in violations if v.rule == "bridge_key_in_entity_types"]
    assert len(matches) == 1
    assert matches[0].severity == "error"
    assert "BOGUS_TAG" in matches[0].item
    assert matches[0].file == "_TEST_BRIDGE"


def test_detects_bridge_value_outside_entity_universe() -> None:
    violations = check_bridge_constant_coverage(
        _entity_types(),
        {"_TEST_BRIDGE": {"PRD": "NotARealCanonicalType"}},
    )
    matches = [v for v in violations if v.rule == "bridge_value_in_entity_universe"]
    assert len(matches) == 1
    assert matches[0].severity == "error"
    assert "NotARealCanonicalType" in matches[0].item


def test_clean_bridge_constant_passes() -> None:
    violations = check_bridge_constant_coverage(
        _entity_types(),
        {"_TEST_BRIDGE": {"PRD": "Product", "BRD": "Brand"}},
    )
    assert violations == []


def test_real_bridge_constants_are_covered_by_entity_types() -> None:
    """Current-state pin: the real _NER_TO_CANONICAL_TYPE (run_daily_pipeline)
    and _KG_TYPE_TO_GR_TYPE (kg/adapter) constants are fully covered by the
    real kg_entity_types.json. If this fails, a bridge map and the type
    registry drifted apart."""
    from src.common.config_loader import load_json

    bridges = _load_bridge_constants()
    assert set(bridges) == {"_NER_TO_CANONICAL_TYPE", "_KG_TYPE_TO_GR_TYPE"}
    assert all(bridge for bridge in bridges.values()), "bridge maps must be non-empty"
    violations = check_bridge_constant_coverage(load_json("kg_entity_types.json"), bridges)
    assert violations == [], f"bridge constants drifted from kg_entity_types.json: {violations}"
