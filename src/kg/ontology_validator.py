"""
Phase 4.3: ontology integration validator (cross-check CI tool).

Cross-checks the 4 core KG ontology config files for internal consistency —
fable_doc/03_improvement_plan.md Phase 4.3 / fable_doc/02_issues_assessment.md
issue A2 ("온톨로지 비정형... 파일 간 정합성 자동 검증이 없다").

Config files covered:
  - configs/kg_entity_types.json     — NER/BEE raw extraction type registry
  - configs/kg_relation_types.json   — canonical relation/predicate registry
  - configs/predicate_contracts.csv  — per-predicate allowed subject/object
                                        types + Layer-3 projectability flag
  - configs/projection_registry.csv  — Layer 2 -> Layer 2.5 signal projection
                                        rules

Checks performed:
  (a) every predicate in predicate_contracts.csv exists in
      kg_relation_types.json (types[].code or special_edges)
  (b) every subject/object type referenced by predicate_contracts.csv exists
      in the entity/concept type universe derived from kg_entity_types.json
      (see "Design note" below)
  (c) every input_predicate in projection_registry.csv exists in
      kg_relation_types.json; and for rows that define an active Layer-2.5
      projection (non-blank output_edge_type), the matching
      predicate_contracts.csv row (matched by predicate) must allow
      projection (projectable_to_layer3 == "Y") and its subject_type/
      object_type must fall within that contract's allowed types
  (d) every output_dst_type in projection_registry.csv (when present) exists
      in the same entity/concept type universe as (b)
  (e) no duplicate definitions: kg_entity_types.json codes; kg_relation_types
      codes; predicate_contracts.csv predicates; projection_registry.csv
      (input_predicate, subject_type, object_type, polarity) rows.
      Relation-code / predicate comparisons here are case-insensitive,
      mirroring checks (a)/(c)'s case-insensitive predicate-existence
      comparison (see `_relation_universe`) — otherwise the same predicate
      defined under two casings (e.g. `has_attribute` / `HAS_ATTRIBUTE`)
      could satisfy existence checks on both sides while slipping past
      duplicate detection here. Entity-type-code and subject_type/object_type
      comparisons stay case-sensitive, mirroring (b)/(d).

Explicitly OUT OF SCOPE for this module (Phase 4.3 task boundary):
  - Dictionary yaml concept references (concern_dict.yaml, goal_alias_map.yaml,
    keyword_surface_map.yaml, etc.) — a separate, later validation surface.
  - Auto-generated ontology documentation.
  - Fixing configs/ — this module only detects and reports; violations found
    in the current configs are surfaced, not corrected here.

Design note on (b)/(d) — the "entity type universe":
  kg_entity_types.json's own header comment scopes it to "NER/BEE 엔티티 타입
  정의": a raw extraction-tag registry (11 NER tags + BEE_ATTR groups). It does
  NOT enumerate the broader canonical fact-ontology vocabulary that
  predicate_contracts.csv / projection_registry.csv actually reference
  (Product, Brand, Concern, Goal, Tool, UserSegment, ...) — most of that
  vocabulary is produced by later normalization/synthesis stages (concept
  dictionaries, user-profile derivation, product-master ingestion), not by
  raw extraction. This split is real and already visible elsewhere in the
  codebase: `NERType` vs `EntityType`/`ConceptType` in src/common/enums.py,
  and the scattered NER->canonical maps in src/kg/adapter.py
  (`_KG_TYPE_TO_GR_TYPE`), src/normalize/ner_normalizer.py
  (`_NER_TO_CANONICAL`), src/jobs/run_daily_pipeline.py
  (`_NER_TO_CANONICAL_TYPE`) — themselves candidates for future consolidation
  (fable_doc issue A2).

  A literal 1:1 string match between kg_entity_types.json codes and
  predicate_contracts.csv/projection_registry.csv types would flag the
  entire canonical vocabulary as "missing" and provide no real signal. To
  make (b)/(d) a meaningful check, this validator computes a type universe
  as the union of:
    1. kg_entity_types.json codes, as-is (raw extraction tags).
    2. the well-established translation of extraction tags (`code` or
       `neo4j_label`) to their canonical fact-layer name — mirrors the
       existing convention already used identically in multiple places
       (see `_EXTRACTION_TYPE_TRANSLATION` below). Only applied when the
       source tag is actually present in kg_entity_types.json.
    3. an explicit allowlist of concept-plane types that are legitimately
       synthesized downstream and never raw-extracted
       (`_NON_EXTRACTED_CONCEPT_TYPES` below).
  A type outside this union is flagged as a violation — this still catches
  real typos/drift in new config rows while not flagging the known,
  intentional two-tier design as a wall of false positives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.config_loader import load_csv, load_json


@dataclass(frozen=True)
class OntologyViolation:
    """One cross-config ontology inconsistency.

    `rule` is a short, stable machine-readable id for the specific check that
    produced this violation (useful for tests/tooling to filter by check).
    `file` is the config file the violation is reported against. `item`
    identifies the specific predicate/type/row at fault. `reason` is a
    human-readable explanation.
    """

    rule: str
    file: str
    item: str
    reason: str


# ---------------------------------------------------------------------------
# Entity/concept type universe (see module docstring "Design note")
# ---------------------------------------------------------------------------

# raw kg_entity_types.json tag (`code` or, for the BEE group, `neo4j_label`)
# -> canonical fact-ontology type name used by predicate_contracts.csv /
# projection_registry.csv. Mirrors src/kg/adapter.py's _KG_TYPE_TO_GR_TYPE.
_EXTRACTION_TYPE_TRANSLATION: dict[str, str] = {
    "PRD": "Product",
    "PER": "ReviewerProxy",
    "BRD": "Brand",
    "CAT": "Category",
    "ING": "Ingredient",
    "DATE": "TemporalContext",
    "COL": "Color",
    "AGE": "AgeBand",
    "VOL": "Volume",
    "EVN": "Event",
    "KEYWORD": "Keyword",
    "BEE_ATTR": "BEEAttr",  # shared neo4j_label of every is_bee entry
}

# Concept-plane types synthesized downstream of raw NER/BEE extraction
# (dictionaries, user-profile derivation, product-master ingestion). These
# are intentionally absent from kg_entity_types.json — see module docstring.
_NON_EXTRACTED_CONCEPT_TYPES: frozenset[str] = frozenset({
    "Concern", "Goal", "Tool", "UserSegment", "PriceBand", "Duration",
    "Frequency", "Person", "Collection", "AliasOrKeyword", "Effect",
    "UsageTarget",
})


def _entity_type_universe(entity_types_cfg: dict[str, Any]) -> set[str]:
    """Recognized entity/concept type names for checks (b) and (d)."""
    types_list = entity_types_cfg.get("types", []) if isinstance(entity_types_cfg, dict) else []
    codes: set[str] = set()
    labels: set[str] = set()
    for t in types_list:
        if not isinstance(t, dict):
            continue
        code = t.get("code")
        if code:
            codes.add(code)
        label = t.get("neo4j_label")
        if label:
            labels.add(label)

    universe = set(codes)
    for raw_tag, canonical in _EXTRACTION_TYPE_TRANSLATION.items():
        if raw_tag in codes or raw_tag in labels:
            universe.add(canonical)
    universe |= _NON_EXTRACTED_CONCEPT_TYPES
    return universe


def _relation_universe(relation_types_cfg: dict[str, Any]) -> set[str]:
    """Recognized relation/predicate codes (lower-cased) for checks (a)/(c).

    Includes both the standard `types[].code` list and `special_edges` keys
    (e.g. OFFICIAL_BRAND is defined under special_edges, not types[]).
    Comparison is case-insensitive: the same predicate is referenced with
    different casing across configs (e.g. `has_keyword` in
    kg_relation_types.json vs `HAS_KEYWORD` in predicate_contracts.csv /
    projection_registry.csv) without there being a real drift bug — the
    existence check should not force a casing convention that isn't
    consistently followed elsewhere in the codebase.
    """
    types_list = relation_types_cfg.get("types", []) if isinstance(relation_types_cfg, dict) else []
    codes = {t.get("code", "") for t in types_list if isinstance(t, dict) and t.get("code")}
    special_edges_cfg = relation_types_cfg.get("special_edges", {}) if isinstance(relation_types_cfg, dict) else {}
    special = {k for k in special_edges_cfg if k != "_comment"} if isinstance(special_edges_cfg, dict) else set()
    return {c.lower() for c in codes} | {c.lower() for c in special}


def _predicate_exists(predicate: str, relation_universe: set[str]) -> bool:
    return predicate.lower() in relation_universe


def _index_contracts_by_predicate(contracts: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """First-row-wins index; true duplicates are separately reported by check (e)."""
    by_predicate: dict[str, dict[str, str]] = {}
    for row in contracts:
        predicate = (row.get("predicate") or "").strip()
        if predicate:
            by_predicate.setdefault(predicate, row)
    return by_predicate


def _split_types(raw: str | None) -> set[str]:
    return {t.strip() for t in (raw or "").split("|") if t.strip()}


# ---------------------------------------------------------------------------
# Checks (a)-(e)
# ---------------------------------------------------------------------------


def _check_predicates_in_relations(
    contracts: list[dict[str, str]],
    relation_universe: set[str],
) -> list[OntologyViolation]:
    """(a) predicate_contracts.csv predicates must exist in kg_relation_types.json."""
    violations: list[OntologyViolation] = []
    for row in contracts:
        predicate = (row.get("predicate") or "").strip()
        if not predicate:
            continue
        if not _predicate_exists(predicate, relation_universe):
            violations.append(OntologyViolation(
                rule="predicate_in_relation_types",
                file="predicate_contracts.csv",
                item=predicate,
                reason=(
                    f"predicate '{predicate}' is not defined in kg_relation_types.json "
                    "(types[].code or special_edges)"
                ),
            ))
    return violations


def _check_contract_types_in_entities(
    contracts: list[dict[str, str]],
    entity_universe: set[str],
) -> list[OntologyViolation]:
    """(b) predicate_contracts.csv subject/object types must exist in kg_entity_types.json."""
    violations: list[OntologyViolation] = []
    for row in contracts:
        predicate = (row.get("predicate") or "").strip()
        for column in ("allowed_subject_types", "allowed_object_types"):
            for type_name in _split_types(row.get(column)):
                if type_name not in entity_universe:
                    violations.append(OntologyViolation(
                        rule="contract_type_in_entity_universe",
                        file="predicate_contracts.csv",
                        item=f"{predicate}.{column}={type_name}",
                        reason=(
                            f"type '{type_name}' referenced by predicate '{predicate}' "
                            f"({column}) is not a recognized entity/concept type "
                            "(kg_entity_types.json code, its canonical translation, or a "
                            "known non-extracted concept type)"
                        ),
                    ))
    return violations


def _check_projection_predicates_and_projectability(
    projections: list[dict[str, str]],
    relation_universe: set[str],
    contract_by_predicate: dict[str, dict[str, str]],
) -> list[OntologyViolation]:
    """(c) projection_registry.csv input_predicate existence + projectability.

    For every row: input_predicate must exist in kg_relation_types.json. For
    rows that define an active Layer-2.5 projection (non-blank
    output_edge_type — administrative rows like DROP/KEEP_CANONICAL_ONLY
    intentionally leave this blank and are not projections at all), the
    matching predicate_contracts.csv row (by predicate) must exist, allow
    projection (projectable_to_layer3 == "Y"), and list this row's
    subject_type/object_type among its allowed types.
    """
    violations: list[OntologyViolation] = []
    for row in projections:
        predicate = (row.get("input_predicate") or "").strip()
        if not predicate:
            continue
        subject_type = (row.get("subject_type") or "").strip()
        object_type = (row.get("object_type") or "").strip()
        polarity = (row.get("polarity") or "").strip()
        item = f"{predicate}({subject_type}->{object_type}, polarity={polarity!r})"

        if not _predicate_exists(predicate, relation_universe):
            violations.append(OntologyViolation(
                rule="projection_predicate_in_relation_types",
                file="projection_registry.csv",
                item=item,
                reason=f"input_predicate '{predicate}' is not defined in kg_relation_types.json",
            ))
            continue  # projectability cannot be evaluated against an undefined predicate

        output_edge_type = (row.get("output_edge_type") or "").strip()
        if not output_edge_type:
            continue  # administrative row (DROP/KEEP_CANONICAL_ONLY/...) — no active projection

        contract = contract_by_predicate.get(predicate)
        if contract is None:
            violations.append(OntologyViolation(
                rule="projection_contract_exists",
                file="projection_registry.csv",
                item=item,
                reason=(
                    f"input_predicate '{predicate}' defines an active projection "
                    f"(output_edge_type={output_edge_type!r}) but has no matching row "
                    "in predicate_contracts.csv"
                ),
            ))
            continue

        if (contract.get("projectable_to_layer3") or "").strip() != "Y":
            violations.append(OntologyViolation(
                rule="projection_projectable_to_layer3",
                file="projection_registry.csv",
                item=item,
                reason=(
                    f"input_predicate '{predicate}' defines an active projection "
                    f"(output_edge_type={output_edge_type!r}) but predicate_contracts.csv "
                    f"has projectable_to_layer3={contract.get('projectable_to_layer3')!r} "
                    "(expected 'Y')"
                ),
            ))

        allowed_subject = _split_types(contract.get("allowed_subject_types"))
        if subject_type and allowed_subject and subject_type not in allowed_subject:
            violations.append(OntologyViolation(
                rule="projection_subject_type_in_contract",
                file="projection_registry.csv",
                item=item,
                reason=(
                    f"subject_type '{subject_type}' is not in predicate_contracts.csv "
                    f"allowed_subject_types for '{predicate}' ({sorted(allowed_subject)})"
                ),
            ))

        allowed_object = _split_types(contract.get("allowed_object_types"))
        if object_type and allowed_object and object_type not in allowed_object:
            violations.append(OntologyViolation(
                rule="projection_object_type_in_contract",
                file="projection_registry.csv",
                item=item,
                reason=(
                    f"object_type '{object_type}' is not in predicate_contracts.csv "
                    f"allowed_object_types for '{predicate}' ({sorted(allowed_object)})"
                ),
            ))
    return violations


def _check_projection_dst_types_in_entities(
    projections: list[dict[str, str]],
    entity_universe: set[str],
) -> list[OntologyViolation]:
    """(d) projection_registry.csv output_dst_type must exist in the entity type universe."""
    violations: list[OntologyViolation] = []
    for row in projections:
        dst_type = (row.get("output_dst_type") or "").strip()
        if not dst_type:
            continue
        if dst_type not in entity_universe:
            predicate = (row.get("input_predicate") or "").strip()
            violations.append(OntologyViolation(
                rule="projection_dst_type_in_entity_universe",
                file="projection_registry.csv",
                item=f"{predicate}.output_dst_type={dst_type}",
                reason=(
                    f"output_dst_type '{dst_type}' (predicate '{predicate}') is not a "
                    "recognized entity/concept type"
                ),
            ))
    return violations


def _find_duplicates(items: list[Any]) -> list[Any]:
    """Stable-ordered list of values that appear more than once in `items`."""
    seen: set[Any] = set()
    duplicates: list[Any] = []
    seen_duplicates: set[Any] = set()
    for item in items:
        if item in seen and item not in seen_duplicates:
            duplicates.append(item)
            seen_duplicates.add(item)
        seen.add(item)
    return duplicates


def _check_duplicates(
    entity_types_cfg: dict[str, Any],
    relation_types_cfg: dict[str, Any],
    contracts: list[dict[str, str]],
    projections: list[dict[str, str]],
) -> list[OntologyViolation]:
    """(e) duplicate definitions across all 4 config files."""
    violations: list[OntologyViolation] = []

    entity_types_list = entity_types_cfg.get("types", []) if isinstance(entity_types_cfg, dict) else []
    entity_codes = [t.get("code", "") for t in entity_types_list if isinstance(t, dict) and t.get("code")]
    for code in _find_duplicates(entity_codes):
        violations.append(OntologyViolation(
            rule="duplicate_entity_type_code",
            file="kg_entity_types.json",
            item=code,
            reason=f"entity type code '{code}' is defined more than once",
        ))

    # Relation codes are lower-cased before dedup — mirrors _relation_universe's
    # case-insensitive comparison (checks (a)/(c)) so two casings of the same
    # predicate code (e.g. `has_attribute` / `HAS_ATTRIBUTE`) cannot both
    # silently pass here while satisfying predicate-existence checks elsewhere.
    relation_types_list = relation_types_cfg.get("types", []) if isinstance(relation_types_cfg, dict) else []
    relation_codes = [
        t.get("code", "").lower() for t in relation_types_list if isinstance(t, dict) and t.get("code")
    ]
    for code in _find_duplicates(relation_codes):
        violations.append(OntologyViolation(
            rule="duplicate_relation_code",
            file="kg_relation_types.json",
            item=code,
            reason=f"relation code '{code}' is defined more than once (case-insensitive)",
        ))

    # Predicates are lower-cased before dedup — same rationale as relation_codes.
    predicates = [row.get("predicate", "").lower() for row in contracts if row.get("predicate")]
    for predicate in _find_duplicates(predicates):
        violations.append(OntologyViolation(
            rule="duplicate_contract_predicate",
            file="predicate_contracts.csv",
            item=predicate,
            reason=f"predicate '{predicate}' has more than one contract row (case-insensitive)",
        ))

    projection_keys = [
        (
            # predicate component lower-cased for dedup (same rationale as
            # above); subject_type/object_type stay case-sensitive, matching
            # the entity-type universe's own case-sensitive comparison.
            row.get("input_predicate", "").lower(),
            row.get("subject_type", ""),
            row.get("object_type", ""),
            row.get("polarity", ""),
        )
        for row in projections
    ]
    for predicate, subject_type, object_type, polarity in _find_duplicates(projection_keys):
        violations.append(OntologyViolation(
            rule="duplicate_projection_row",
            file="projection_registry.csv",
            item=f"{predicate}({subject_type}->{object_type}, polarity={polarity!r})",
            reason=(
                f"projection rule for predicate='{predicate}' subject_type='{subject_type}' "
                f"object_type='{object_type}' polarity='{polarity}' is defined more than once"
            ),
        ))

    return violations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_ontology(
    entity_types_cfg: dict[str, Any],
    relation_types_cfg: dict[str, Any],
    contracts: list[dict[str, str]],
    projections: list[dict[str, str]],
) -> list[OntologyViolation]:
    """Cross-check 4 already-loaded ontology config structures.

    Pure function (no file I/O) so callers/tests can inject synthetic or
    deliberately-broken config structures. Returns a structured list of
    `OntologyViolation`; an empty list means no violation was found.
    """
    entity_universe = _entity_type_universe(entity_types_cfg)
    relation_universe = _relation_universe(relation_types_cfg)
    contract_by_predicate = _index_contracts_by_predicate(contracts)

    violations: list[OntologyViolation] = []
    violations.extend(_check_predicates_in_relations(contracts, relation_universe))
    violations.extend(_check_contract_types_in_entities(contracts, entity_universe))
    violations.extend(_check_projection_predicates_and_projectability(
        projections, relation_universe, contract_by_predicate,
    ))
    violations.extend(_check_projection_dst_types_in_entities(projections, entity_universe))
    violations.extend(_check_duplicates(entity_types_cfg, relation_types_cfg, contracts, projections))
    return violations


def validate_current_ontology_configs() -> list[OntologyViolation]:
    """Load the 4 core ontology configs from configs/ and cross-validate them.

    Convenience I/O wrapper around `validate_ontology` — the CI entry point.
    """
    entity_types_cfg = load_json("kg_entity_types.json")
    relation_types_cfg = load_json("kg_relation_types.json")
    contracts = load_csv("predicate_contracts.csv")
    projections = load_csv("projection_registry.csv")
    return validate_ontology(entity_types_cfg, relation_types_cfg, contracts, projections)
