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
  - configs/relation_canonical_map.json — label->canonical identity map,
                                        carries a self-declared meta count
                                        (v2 check (f))

v2 extension (Phase 7 A5) — beyond the static cross-config checks (a)-(e),
four additional detectors surface vocabulary drift and dead vocabulary. They
carry a `severity` on each `OntologyViolation`: (f)/(i) are ERROR-severity
(they join the CI gate `validate_current_ontology_configs`), while (g) and the
liveness report are WARNING-severity (informational; never fail CI because
they are data-dependent):
  (f) relation_canonical_map.json's declared meta.total_labels must equal the
      actual number of label_to_canonical entries (ERROR).
  (g) orphan entity types — a raw-extracted concept type (a node IS created
      for it) that is referenced by NO predicate_contracts.csv row (neither
      subject nor object) can never form a projectable edge (WARNING). Current
      configs surface exactly Color/Volume/AgeBand/Event.
  (h) vocabulary liveness — signal families / object types defined in
      projection_registry.csv but generated 0 times by an actual demo-fixture
      pipeline run (WARNING, data-dependent). Not part of the static CI step
      because it must execute the pipeline; exposed via `collect_liveness_report`
      and the `validate-ontology --liveness` CLI flag.
  (i) 3-layer bridge constant coverage — the hardcoded NER/KG->canonical type
      bridge maps (`src/jobs/run_daily_pipeline._NER_TO_CANONICAL_TYPE`,
      `src/kg/adapter._KG_TYPE_TO_GR_TYPE`) must reference only recognized
      kg_entity_types.json tags (keys) and canonical types (values) (ERROR).

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
    human-readable explanation. `severity` is `"error"` (CI-failing, the
    default so every existing construction site keeps its meaning) or
    `"warning"` (informational — surfaced but never fails the CI gate; used by
    the v2 vocabulary-liveness checks that are data-dependent by nature).
    """

    rule: str
    file: str
    item: str
    reason: str
    severity: str = "error"


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
# v2 checks (f)-(i) — vocabulary consistency & liveness (Phase 7 A5)
# ---------------------------------------------------------------------------


def check_canonical_map_meta(
    canonical_map_cfg: dict[str, Any],
) -> list[OntologyViolation]:
    """(f) relation_canonical_map.json meta.total_labels vs actual entry count.

    The file carries a self-declared `meta.total_labels`; it must equal the
    number of `label_to_canonical` entries. A mismatch (the historical 65-vs-68
    drift) means the count was hand-edited out of step — the exact class of
    silent inconsistency this validator exists to catch. ERROR severity.
    """
    if not isinstance(canonical_map_cfg, dict):
        return []
    label_map = canonical_map_cfg.get("label_to_canonical")
    if not isinstance(label_map, dict):
        return []
    actual = len(label_map)
    meta = canonical_map_cfg.get("meta")
    declared = meta.get("total_labels") if isinstance(meta, dict) else None
    if declared is None:
        return []
    if declared != actual:
        return [OntologyViolation(
            rule="canonical_map_meta_count",
            file="relation_canonical_map.json",
            item=f"meta.total_labels={declared}",
            reason=(
                f"meta.total_labels declares {declared} but label_to_canonical "
                f"has {actual} entries"
            ),
            severity="error",
        )]
    return []


def check_orphan_entity_types(
    entity_types_cfg: dict[str, Any],
    contracts: list[dict[str, str]],
) -> list[OntologyViolation]:
    """(g) orphan entity types — nodes created but no predicate references them.

    A raw-extracted concept type (present in kg_entity_types.json, so a node IS
    created for it) that appears in NO predicate_contracts.csv row — neither
    allowed_subject_types nor allowed_object_types — can never form a valid,
    let alone projectable, edge. Its nodes are dead weight and any NLP relation
    touching them is quarantined (e.g. `has_attribute|Product->Color`). WARNING
    severity: this is a real design smell but not a config typo, and clearing
    it is a modelling decision (add a contract or stop extracting the type).

    Current configs surface exactly Color/Volume/AgeBand/Event.
    """
    types_list = entity_types_cfg.get("types", []) if isinstance(entity_types_cfg, dict) else []
    codes: set[str] = set()
    labels: set[str] = set()
    for t in types_list:
        if not isinstance(t, dict):
            continue
        if t.get("code"):
            codes.add(t["code"])
        if t.get("neo4j_label"):
            labels.add(t["neo4j_label"])

    # canonical types that are actually produced by raw extraction (a node is
    # created), keyed back to the raw tag for a legible reason string.
    extracted: dict[str, str] = {
        canonical: raw_tag
        for raw_tag, canonical in _EXTRACTION_TYPE_TRANSLATION.items()
        if raw_tag in codes or raw_tag in labels
    }

    referenced: set[str] = set()
    for row in contracts:
        referenced |= _split_types(row.get("allowed_subject_types"))
        referenced |= _split_types(row.get("allowed_object_types"))

    violations: list[OntologyViolation] = []
    for canonical, raw_tag in sorted(extracted.items()):
        if canonical not in referenced:
            violations.append(OntologyViolation(
                rule="orphan_entity_type",
                file="predicate_contracts.csv",
                item=canonical,
                reason=(
                    f"entity type '{canonical}' is raw-extracted (nodes created "
                    f"via tag '{raw_tag}') but referenced by no predicate_contracts.csv "
                    "row (neither subject nor object) — it can never form a "
                    "projectable edge"
                ),
                severity="warning",
            ))
    return violations


def check_bridge_constant_coverage(
    entity_types_cfg: dict[str, Any],
    bridge_constants: dict[str, dict[str, str]],
) -> list[OntologyViolation]:
    """(i) 3-layer bridge constants must stay in sync with kg_entity_types.json.

    The hardcoded NER/KG->canonical type maps scattered in the pipeline
    (`_NER_TO_CANONICAL_TYPE`, `_KG_TYPE_TO_GR_TYPE`) are a known consolidation
    debt (module docstring / fable_doc issue A2). Until they are unified, this
    check pins them: every KEY must be a recognized raw kg_entity_types.json
    tag (code or neo4j_label) and every VALUE must be a recognized canonical
    type. Catches drift where a bridge maps a tag the registry never defines,
    or emits a canonical type outside the known universe. ERROR severity.

    `bridge_constants` maps a constant's display name to its dict, so callers
    (and tests) inject the real or synthetic maps without this module importing
    the heavy pipeline modules at import time.
    """
    types_list = entity_types_cfg.get("types", []) if isinstance(entity_types_cfg, dict) else []
    raw_tags: set[str] = set()
    for t in types_list:
        if not isinstance(t, dict):
            continue
        if t.get("code"):
            raw_tags.add(t["code"])
        if t.get("neo4j_label"):
            raw_tags.add(t["neo4j_label"])
    universe = _entity_type_universe(entity_types_cfg)

    violations: list[OntologyViolation] = []
    for const_name, mapping in sorted(bridge_constants.items()):
        for raw_tag, canonical in mapping.items():
            if raw_tag not in raw_tags:
                violations.append(OntologyViolation(
                    rule="bridge_key_in_entity_types",
                    file=const_name,
                    item=f"{const_name}[{raw_tag!r}]",
                    reason=(
                        f"bridge key '{raw_tag}' is not a recognized kg_entity_types.json "
                        "tag (code or neo4j_label)"
                    ),
                    severity="error",
                ))
            if canonical not in universe:
                violations.append(OntologyViolation(
                    rule="bridge_value_in_entity_universe",
                    file=const_name,
                    item=f"{const_name}[{raw_tag!r}]={canonical}",
                    reason=(
                        f"bridge value '{canonical}' (key '{raw_tag}') is not a "
                        "recognized entity/concept type"
                    ),
                    severity="error",
                ))
    return violations


# ---------------------------------------------------------------------------
# (h) vocabulary liveness report — data-dependent, WARNING-only
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LivenessReport:
    """Vocabulary defined in projection_registry.csv vs actually generated by a
    demo-fixture pipeline run. `dead_*` are the defined-but-never-generated
    entries; they are WARNING material, not CI failures, because whether a
    family/type fires is data-dependent.
    """

    fixture: str
    kg_mode: str
    total_signals: int
    defined_signal_families: list[str]
    generated_signal_families: list[str]
    dead_signal_families: list[str]
    defined_object_types: list[str]
    generated_object_types: list[str]
    dead_object_types: list[str]

    def warnings(self) -> list[OntologyViolation]:
        violations: list[OntologyViolation] = []
        for family in self.dead_signal_families:
            violations.append(OntologyViolation(
                rule="dead_signal_family",
                file="projection_registry.csv",
                item=family,
                reason=(
                    f"signal family '{family}' is defined in projection_registry.csv "
                    f"but 0 signals were generated on fixture '{self.fixture}' "
                    f"(kg_mode={self.kg_mode})"
                ),
                severity="warning",
            ))
        for object_type in self.dead_object_types:
            violations.append(OntologyViolation(
                rule="dead_object_type",
                file="projection_registry.csv",
                item=object_type,
                reason=(
                    f"object type '{object_type}' is a defined projection output_dst_type "
                    f"but 0 signals produced it on fixture '{self.fixture}' "
                    f"(kg_mode={self.kg_mode})"
                ),
                severity="warning",
            ))
        return violations


def build_liveness_report(
    *,
    fixture: str,
    kg_mode: str,
    total_signals: int,
    defined_signal_families: set[str],
    generated_signal_families: set[str],
    defined_object_types: set[str],
    generated_object_types: set[str],
) -> LivenessReport:
    """Pure diff step — testable without running the pipeline."""
    return LivenessReport(
        fixture=fixture,
        kg_mode=kg_mode,
        total_signals=total_signals,
        defined_signal_families=sorted(defined_signal_families),
        generated_signal_families=sorted(generated_signal_families),
        dead_signal_families=sorted(defined_signal_families - generated_signal_families),
        defined_object_types=sorted(defined_object_types),
        generated_object_types=sorted(generated_object_types),
        dead_object_types=sorted(defined_object_types - generated_object_types),
    )


def collect_liveness_report(
    *,
    fixture: str = "dense_golden",
    kg_mode: str = "on",
) -> LivenessReport:
    """(h) Run the in-memory demo pipeline and diff generated vs defined vocab.

    Executes `run_full_load` over a fixture (the same primitive the audit uses;
    no DB, no network, no src/rec scoring) and collects the signal families and
    destination types that actually appeared on emitted signals, then diffs them
    against projection_registry.csv's defined vocabulary. Heavy (runs the
    pipeline), so imports are deferred and this is NOT part of the static CI
    step — it backs `validate-ontology --liveness` for manual/documented use.
    """
    import contextlib
    import io
    import json as _json
    from pathlib import Path

    from src.common.config_loader import load_csv
    from src.jobs.run_full_load import FullLoadConfig, run_full_load

    root = Path(__file__).resolve().parents[2]
    fixture_dirs = {
        "wide": root / "mockdata",
        "dense_golden": root / "mockdata" / "dense_golden",
    }
    fixture_dir = fixture_dirs.get(fixture)
    if fixture_dir is None:
        raise ValueError(f"unknown fixture: {fixture} (choose from {sorted(fixture_dirs)})")

    products = _json.loads((fixture_dir / "product_catalog_es.json").read_text(encoding="utf-8"))
    users = _json.loads((fixture_dir / "user_profiles_normalized.json").read_text(encoding="utf-8"))

    with contextlib.redirect_stdout(io.StringIO()):
        result = run_full_load(FullLoadConfig(
            review_json_path=str(fixture_dir / "review_triples_raw.json"),
            product_es_records=products,
            user_profiles=users,
            kg_mode=kg_mode,
        ))

    generated_families: set[str] = set()
    generated_object_types: set[str] = set()
    for bundle in result.batch_result.get("all_bundles", []):
        for signal in getattr(bundle, "wrapped_signals", []):
            family = getattr(signal, "signal_family", None)
            dst_type = getattr(signal, "dst_type", None)
            if family:
                generated_families.add(str(family))
            if dst_type:
                generated_object_types.add(str(dst_type))

    projections = load_csv("projection_registry.csv")
    defined_families = {
        (row.get("output_signal_family") or "").strip()
        for row in projections
        if (row.get("output_signal_family") or "").strip()
    }
    defined_object_types = {
        (row.get("output_dst_type") or "").strip()
        for row in projections
        if (row.get("output_dst_type") or "").strip()
    }

    return build_liveness_report(
        fixture=fixture,
        kg_mode=kg_mode,
        total_signals=result.signal_count,
        defined_signal_families=defined_families,
        generated_signal_families=generated_families,
        defined_object_types=defined_object_types,
        generated_object_types=generated_object_types,
    )


def _load_bridge_constants() -> dict[str, dict[str, str]]:
    """Import the real 3-layer bridge maps for check (i).

    Deferred import: keeps `import src.kg.ontology_validator` cheap and avoids
    coupling the module's import graph to the daily pipeline. src/jobs and
    src/kg are not part of the concurrently-edited surface.
    """
    from src.jobs.run_daily_pipeline import _NER_TO_CANONICAL_TYPE
    from src.kg.adapter import _KG_TYPE_TO_GR_TYPE

    return {
        "_NER_TO_CANONICAL_TYPE": dict(_NER_TO_CANONICAL_TYPE),
        "_KG_TYPE_TO_GR_TYPE": dict(_KG_TYPE_TO_GR_TYPE),
    }


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
    """Load the core ontology configs and return CI-gating (ERROR) violations.

    The CI entry point. Extends the static `validate_ontology` cross-checks
    (a)-(e) with the v2 ERROR-severity checks (f) relation_canonical_map.json
    meta count and (i) 3-layer bridge-constant coverage. WARNING-severity
    findings (orphan types, liveness) are intentionally excluded here so this
    stays an empty-list-means-clean gate — use `collect_ontology_warnings()`
    and `collect_liveness_report()` for those.
    """
    entity_types_cfg = load_json("kg_entity_types.json")
    relation_types_cfg = load_json("kg_relation_types.json")
    contracts = load_csv("predicate_contracts.csv")
    projections = load_csv("projection_registry.csv")
    canonical_map_cfg = load_json("relation_canonical_map.json")

    violations: list[OntologyViolation] = []
    violations.extend(validate_ontology(entity_types_cfg, relation_types_cfg, contracts, projections))
    violations.extend(check_canonical_map_meta(canonical_map_cfg))
    violations.extend(check_bridge_constant_coverage(entity_types_cfg, _load_bridge_constants()))
    return [v for v in violations if v.severity == "error"]


def collect_ontology_warnings() -> list[OntologyViolation]:
    """Static WARNING-severity findings that never fail the CI gate.

    Currently (g) orphan entity types. Data-independent (pure config analysis),
    so safe to compute in-process and display alongside the CI gate. The (h)
    liveness warnings require an actual pipeline run — see
    `collect_liveness_report`.
    """
    entity_types_cfg = load_json("kg_entity_types.json")
    contracts = load_csv("predicate_contracts.csv")
    return check_orphan_entity_types(entity_types_cfg, contracts)
