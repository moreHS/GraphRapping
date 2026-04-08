"""Tests: BEE target attribution — unit tests for attribution helper.

Verifies the 5 attribution scenarios from the instruction document:
1. target-linked BEE (direct relation to target product)
2. 타제품 BEE (relation to different product)
3. relation 없는 BEE (no anchor)
4. placeholder-resolved BEE
5. same-entity-resolved BEE
"""
from src.link.bee_attribution import attribute_bee_rows, BeeAttribution
from src.common.enums import AttributionSource


def _bee(phrase, attr, start=None, end=None):
    return {
        "phrase_text": phrase,
        "bee_attr_raw": attr,
        "start_offset": start,
        "end_offset": end,
    }


def _rel(subj_text, obj_text, obj_group, source_type="NER-BeE",
         subj_start=None, subj_end=None, obj_start=None, obj_end=None):
    return {
        "subj_text": subj_text,
        "obj_text": obj_text,
        "subj_group": "PRD",
        "obj_group": obj_group,
        "relation_raw": "has_attribute",
        "source_type": source_type,
        "subj_start": subj_start, "subj_end": subj_end,
        "obj_start": obj_start, "obj_end": obj_end,
    }


# --- Scenario 1: direct_rel via target product name ---

def test_direct_rel_target_product():
    """BEE with NER-BEE relation where subject IS the target product → direct_rel."""
    bees = [_bee("촉촉하고 흡수가 빨라요", "보습력")]
    rels = [_rel("라네즈 에센스", "촉촉하고 흡수가 빨라요", "보습력")]
    results = attribute_bee_rows(bees, rels, target_product_name="라네즈 에센스")
    assert len(results) == 1
    assert results[0].target_linked is True
    assert results[0].attribution_source == AttributionSource.DIRECT_REL


# --- Scenario 2: 타제품 BEE (different product) ---

def test_other_product_bee():
    """BEE with relation to a different product → unlinked."""
    bees = [_bee("발림성 좋아요", "발림성")]
    rels = [_rel("헤라 쿠션", "발림성 좋아요", "발림성")]
    results = attribute_bee_rows(bees, rels, target_product_name="라네즈 에센스")
    assert len(results) == 1
    assert results[0].target_linked is False
    assert results[0].attribution_source == AttributionSource.UNLINKED


# --- Scenario 3: relation 없는 BEE ---

def test_unlinked_no_relation():
    """BEE with no matching NER-BEE relation → unlinked."""
    bees = [_bee("향이 좋아요", "향")]
    rels = []  # No relations at all
    results = attribute_bee_rows(bees, rels)
    assert len(results) == 1
    assert results[0].target_linked is False
    assert results[0].attribution_source == AttributionSource.UNLINKED
    assert results[0].attribution_confidence == 0.0


# --- Scenario 4: placeholder-resolved (Review Target) ---

def test_placeholder_review_target():
    """BEE with subject='Review Target' → placeholder_resolved."""
    bees = [_bee("제형이 가벼워요", "제형")]
    rels = [_rel("Review Target", "제형이 가벼워요", "제형")]
    results = attribute_bee_rows(bees, rels, target_product_name="라네즈 에센스")
    assert len(results) == 1
    assert results[0].target_linked is True
    assert results[0].attribution_source == AttributionSource.PLACEHOLDER_RESOLVED


def test_placeholder_korean_demonstrative():
    """BEE with subject='이 제품' → placeholder_resolved."""
    bees = [_bee("촉촉해요", "보습력")]
    rels = [_rel("이 제품", "촉촉해요", "보습력")]
    results = attribute_bee_rows(bees, rels, target_product_name="설화수 크림")
    assert len(results) == 1
    assert results[0].target_linked is True
    assert results[0].attribution_source == AttributionSource.PLACEHOLDER_RESOLVED


# --- Scenario 5: same-entity resolved ---

def test_same_entity_resolved():
    """BEE with subject resolved to target via same_entity merge."""
    bees = [_bee("보습력이 좋아요", "보습력")]
    rels = [_rel("에센스", "보습력이 좋아요", "보습력")]
    same_entity = [{"subj_text": "에센스", "obj_text": "라네즈 에센스"}]
    results = attribute_bee_rows(bees, rels,
                                target_product_name="라네즈 에센스",
                                same_entity_pairs=same_entity)
    assert len(results) == 1
    assert results[0].target_linked is True
    assert results[0].attribution_source == AttributionSource.SAME_ENTITY_RESOLVED


# --- Edge cases ---

def test_ambiguous_distal_demonstrative():
    """BEE with subject='그 제품' (distal) → unlinked (ambiguous)."""
    bees = [_bee("향이 좋아요", "향")]
    rels = [_rel("그 제품", "향이 좋아요", "향")]
    results = attribute_bee_rows(bees, rels, target_product_name="라네즈 에센스")
    assert len(results) == 1
    assert results[0].target_linked is False
    assert results[0].attribution_source == AttributionSource.UNLINKED


def test_attribution_confidence_values():
    """Attribution confidence should reflect source strength."""
    bees = [_bee("A", "보습력"), _bee("B", "보습력"), _bee("C", "향")]
    rels = [
        _rel("라네즈 에센스", "A", "보습력"),       # direct_rel
        _rel("Review Target", "B", "보습력"),        # placeholder
    ]
    results = attribute_bee_rows(bees, rels, target_product_name="라네즈 에센스")
    assert results[0].attribution_confidence == 1.0   # direct_rel
    assert results[1].attribution_confidence == 0.9   # placeholder
    assert results[2].attribution_confidence == 0.0   # unlinked


def test_multiple_bees_mixed_attribution():
    """Multiple BEEs: some linked, some not."""
    bees = [
        _bee("촉촉해요", "보습력"),
        _bee("발림성 좋아요", "발림성"),
        _bee("향이 좋아요", "향"),
    ]
    rels = [
        _rel("라네즈 에센스", "촉촉해요", "보습력"),
        _rel("헤라 쿠션", "발림성 좋아요", "발림성"),
        # No relation for 향
    ]
    results = attribute_bee_rows(bees, rels, target_product_name="라네즈 에센스")
    assert results[0].target_linked is True   # 보습력 → target
    assert results[1].target_linked is False  # 발림성 → other product
    assert results[2].target_linked is False  # 향 → no relation
