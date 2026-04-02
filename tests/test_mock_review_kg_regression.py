"""Tests: review KG output regression — entity/edge integrity and evidence kind coverage."""
import json
from pathlib import Path

def _load_kg():
    return json.loads(Path("mockdata/review_kg_output.json").read_text(encoding="utf-8"))

def test_edge_entity_refs_exist():
    """All entity IDs referenced in edges must exist in entities."""
    kg = _load_kg()
    entity_ids = {e["entity_id"] for e in kg["entities"]}
    for edge in kg["edges"]:
        assert edge["subj_entity_id"] in entity_ids, f"Edge {edge['edge_id']} subj {edge['subj_entity_id']} missing"
        assert edge["obj_entity_id"] in entity_ids, f"Edge {edge['edge_id']} obj {edge['obj_entity_id']} missing"

def test_evidence_kind_coverage():
    """Must have RAW_REL, NER_BEE_ANCHOR, BEE_SYNTHETIC, AUTO_KEYWORD."""
    kg = _load_kg()
    kinds = {e["evidence_kind"] for e in kg["edges"]}
    assert "RAW_REL" in kinds
    assert "NER_BEE_ANCHOR" in kinds
    assert "BEE_SYNTHETIC" in kinds
    assert "AUTO_KEYWORD" in kinds

def test_confidence_ranges():
    """Confidence values must be within expected ranges per evidence_kind."""
    kg = _load_kg()
    ranges = {
        "RAW_REL": (0.8, 1.0),
        "NER_BEE_ANCHOR": (0.7, 0.9),
        "BEE_SYNTHETIC": (0.3, 0.5),
        "AUTO_KEYWORD": (0.2, 0.4),
    }
    for edge in kg["edges"]:
        kind = edge["evidence_kind"]
        conf = edge["confidence"]
        lo, hi = ranges.get(kind, (0.0, 1.0))
        assert lo <= conf <= hi, f"Edge {edge['edge_id']} kind={kind} conf={conf} out of range [{lo},{hi}]"

def test_entity_type_distribution():
    """Must have at least PRD, BRD, BEE_ATTR, KEYWORD entity types."""
    kg = _load_kg()
    types = {e["entity_type"] for e in kg["entities"]}
    assert "PRD" in types
    assert "BRD" in types
    assert "BEE_ATTR" in types
    assert "KEYWORD" in types

def test_bee_attr_has_polarity():
    """BEE_ATTR entities must have polarity and bee_type."""
    kg = _load_kg()
    for e in kg["entities"]:
        if e["entity_type"] == "BEE_ATTR":
            assert e.get("polarity") in ("POS", "NEG", "NEU"), f"BEE_ATTR {e['entity_id']} missing polarity"
            assert e.get("bee_type"), f"BEE_ATTR {e['entity_id']} missing bee_type"

def test_placeholder_entities_have_scope_key():
    """PRD entities that are placeholders must have scope_key."""
    kg = _load_kg()
    for e in kg["entities"]:
        if e["entity_type"] == "PRD" and e.get("is_placeholder", False):
            assert e.get("scope_key"), f"Placeholder PRD {e['entity_id']} missing scope_key"
