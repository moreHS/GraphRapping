"""
Enums and constants for GraphRapping.
"""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# NER Entity Types (10)
# ---------------------------------------------------------------------------

class NERType(str, Enum):
    PRD = "PRD"    # Product
    PER = "PER"    # Person
    CAT = "CAT"    # Category
    BRD = "BRD"    # Brand
    DATE = "DATE"  # Date/Time
    COL = "COL"    # Color
    AGE = "AGE"    # Age
    VOL = "VOL"    # Volume
    EVN = "EVN"    # Event
    ING = "ING"    # Ingredient


# ---------------------------------------------------------------------------
# Canonical Entity Types (Layer 2)
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    PRODUCT = "Product"
    REVIEWER_PROXY = "ReviewerProxy"
    OTHER_PRODUCT = "OtherProduct"
    BRAND = "Brand"
    CATEGORY = "Category"
    INGREDIENT = "Ingredient"
    BEE_ATTR = "BEEAttr"
    KEYWORD = "Keyword"
    TEMPORAL_CONTEXT = "TemporalContext"
    FREQUENCY = "Frequency"
    DURATION = "Duration"
    ABSOLUTE_DATE = "AbsoluteDate"
    CONCERN = "Concern"
    GOAL = "Goal"
    TOOL = "Tool"
    USER = "User"
    SKIN_TYPE = "SkinType"
    SKIN_TONE = "SkinTone"
    FRAGRANCE = "Fragrance"
    USER_SEGMENT = "UserSegment"
    COLOR = "Color"
    AGE_BAND = "AgeBand"
    VOLUME = "Volume"
    PRICE_BAND = "PriceBand"
    COUNTRY = "Country"


# ---------------------------------------------------------------------------
# Concept Types (Common Concept Layer)
# ---------------------------------------------------------------------------

class ConceptType(str, Enum):
    BRAND = "Brand"
    CATEGORY = "Category"
    INGREDIENT = "Ingredient"
    BEE_ATTR = "BEEAttr"
    KEYWORD = "Keyword"
    TEMPORAL_CONTEXT = "TemporalContext"
    FREQUENCY = "Frequency"
    DURATION = "Duration"
    ABSOLUTE_DATE = "AbsoluteDate"
    CONCERN = "Concern"
    GOAL = "Goal"
    TOOL = "Tool"
    SKIN_TYPE = "SkinType"
    SKIN_TONE = "SkinTone"
    FRAGRANCE = "Fragrance"
    USER_SEGMENT = "UserSegment"
    PRICE_BAND = "PriceBand"
    COUNTRY = "Country"
    AGE_BAND = "AgeBand"


# ---------------------------------------------------------------------------
# Object Reference Kind
# ---------------------------------------------------------------------------

class ObjectRefKind(str, Enum):
    ENTITY = "ENTITY"
    CONCEPT = "CONCEPT"
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    JSON = "JSON"


# ---------------------------------------------------------------------------
# Signal Families (Layer 3)
# ---------------------------------------------------------------------------

class SignalFamily(str, Enum):
    BEE_ATTR = "BEE_ATTR"
    BEE_KEYWORD = "BEE_KEYWORD"
    CONTEXT = "CONTEXT"
    TOOL = "TOOL"
    CONCERN_POS = "CONCERN_POS"
    CONCERN_NEG = "CONCERN_NEG"
    COMPARISON = "COMPARISON"
    COUSED_PRODUCT = "COUSED_PRODUCT"
    SEGMENT = "SEGMENT"
    CATALOG_VALIDATION = "CATALOG_VALIDATION"


class EdgeType(str, Enum):
    HAS_BEE_ATTR_SIGNAL = "HAS_BEE_ATTR_SIGNAL"
    HAS_BEE_KEYWORD_SIGNAL = "HAS_BEE_KEYWORD_SIGNAL"
    USED_IN_CONTEXT_SIGNAL = "USED_IN_CONTEXT_SIGNAL"
    USED_WITH_TOOL_SIGNAL = "USED_WITH_TOOL_SIGNAL"
    USED_WITH_PRODUCT_SIGNAL = "USED_WITH_PRODUCT_SIGNAL"
    ADDRESSES_CONCERN_SIGNAL = "ADDRESSES_CONCERN_SIGNAL"
    MAY_CAUSE_CONCERN_SIGNAL = "MAY_CAUSE_CONCERN_SIGNAL"
    COMPARED_WITH_SIGNAL = "COMPARED_WITH_SIGNAL"
    TARGETED_AT_SEGMENT_SIGNAL = "TARGETED_AT_SEGMENT_SIGNAL"
    RECOMMENDED_TO_SEGMENT_SIGNAL = "RECOMMENDED_TO_SEGMENT_SIGNAL"
    CATALOG_VALIDATION_SIGNAL = "CATALOG_VALIDATION_SIGNAL"


# ---------------------------------------------------------------------------
# Polarity
# ---------------------------------------------------------------------------

class Polarity(str, Enum):
    POS = "POS"
    NEG = "NEG"
    NEU = "NEU"
    MIXED = "MIXED"


SENTIMENT_MAP: dict[str, Polarity] = {
    "긍정": Polarity.POS,
    "부정": Polarity.NEG,
    "중립": Polarity.NEU,
    "혼합": Polarity.MIXED,
    "positive": Polarity.POS,
    "negative": Polarity.NEG,
    "neutral": Polarity.NEU,
    "mixed": Polarity.MIXED,
}


# ---------------------------------------------------------------------------
# DATE sub-types (4-way split)
# ---------------------------------------------------------------------------

class DateSubType(str, Enum):
    TEMPORAL_CONTEXT = "TemporalContext"   # 아침, 세안후, 여름
    FREQUENCY = "Frequency"                # 하루에 1번, 매일
    DURATION = "Duration"                  # 2주째, 한달동안
    ABSOLUTE_DATE = "AbsoluteDate"         # 2024년 여름 세일, 3월 1일


# ---------------------------------------------------------------------------
# Match Status (product matching)
# ---------------------------------------------------------------------------

class MatchStatus(str, Enum):
    EXACT = "EXACT"
    NORM = "NORM"
    ALIAS = "ALIAS"
    FUZZY = "FUZZY"
    QUARANTINE = "QUARANTINE"


# ---------------------------------------------------------------------------
# Identity Stability (reviewer proxy)
# ---------------------------------------------------------------------------

class IdentityStability(str, Enum):
    STABLE = "STABLE"
    REVIEW_LOCAL = "REVIEW_LOCAL"


# ---------------------------------------------------------------------------
# Event Time Source
# ---------------------------------------------------------------------------

class EventTimeSource(str, Enum):
    SOURCE_CREATED = "SOURCE_CREATED"
    COLLECTED_AT = "COLLECTED_AT"
    PROCESSING_TIME = "PROCESSING_TIME"


# ---------------------------------------------------------------------------
# Recommendation Mode
# ---------------------------------------------------------------------------

class RecommendationMode(str, Enum):
    STRICT = "strict"      # category mismatch → zero-out
    EXPLORE = "explore"    # category mismatch → penalty only
    COMPARE = "compare"    # comparison-neighbor allowed


# ---------------------------------------------------------------------------
# Quarantine Status
# ---------------------------------------------------------------------------

class QuarantineStatus(str, Enum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Window Types (aggregation)
# ---------------------------------------------------------------------------

class WindowType(str, Enum):
    D30 = "30d"
    D90 = "90d"
    ALL = "all"


# ---------------------------------------------------------------------------
# User Preference Edge Types
# ---------------------------------------------------------------------------

USER_PREFERENCE_EDGE_TYPES = frozenset({
    "HAS_SKIN_TYPE",
    "HAS_SKIN_TONE",
    "HAS_AGE_BAND",
    "PREFERS_BRAND",
    "PREFERS_CATEGORY",
    "PREFERS_INGREDIENT",
    "AVOIDS_INGREDIENT",
    "HAS_CONCERN",
    "WANTS_GOAL",
    "WANTS_EFFECT",
    "PREFERS_CONTEXT",
    "PREFERS_BEE_ATTR",
    "AVOIDS_BEE_ATTR",
    "PREFERS_KEYWORD",
    "AVOIDS_KEYWORD",
    "SEASONAL_PREFERS_BRAND",
    "SEASONAL_PREFERS_CATEGORY",
    "REPURCHASES_PRODUCT_OR_FAMILY",  # backward compat (no longer generated)
    "REPURCHASES_BRAND",
    "REPURCHASES_CATEGORY",
    "RECENTLY_PURCHASED",
    "OWNS_PRODUCT",
    "OWNS_FAMILY",
    "REPURCHASES_FAMILY",
})

# Fact family groupings for user facts
USER_STATE_EDGE_TYPES = frozenset({"HAS_SKIN_TYPE", "HAS_SKIN_TONE", "HAS_AGE_BAND"})
USER_CONCERN_EDGE_TYPES = frozenset({"HAS_CONCERN"})
USER_GOAL_EDGE_TYPES = frozenset({"WANTS_GOAL", "WANTS_EFFECT"})
USER_CONTEXT_EDGE_TYPES = frozenset({"PREFERS_CONTEXT"})
USER_BEHAVIOR_EDGE_TYPES = frozenset({
    "PREFERS_BRAND", "PREFERS_CATEGORY", "PREFERS_INGREDIENT", "AVOIDS_INGREDIENT",
    "PREFERS_BEE_ATTR", "AVOIDS_BEE_ATTR", "PREFERS_KEYWORD", "AVOIDS_KEYWORD",
    "SEASONAL_PREFERS_BRAND", "SEASONAL_PREFERS_CATEGORY",
    "REPURCHASES_PRODUCT_OR_FAMILY",  # backward compat (no longer generated)
    "REPURCHASES_BRAND", "REPURCHASES_CATEGORY",
    "RECENTLY_PURCHASED", "OWNS_PRODUCT",
    "OWNS_FAMILY", "REPURCHASES_FAMILY",
})


# ---------------------------------------------------------------------------
# Scoring exclusion: these signal families are NOT used in scoring
# ---------------------------------------------------------------------------

SCORING_EXCLUDED_FAMILIES = frozenset({
    SignalFamily.CATALOG_VALIDATION,
})


# ---------------------------------------------------------------------------
# Evidence Kind (KG pipeline source classification)
# ---------------------------------------------------------------------------

class EvidenceKind(str, Enum):
    RAW_REL = "RAW_REL"                # Raw explicit relation (NER-NER)
    NER_BEE_ANCHOR = "NER_BEE_ANCHOR"  # NER-BeE anchored relation
    BEE_SYNTHETIC = "BEE_SYNTHETIC"    # BEE-only auto-generated synthetic
    BEE_DICT = "BEE_DICT"             # BEE with dictionary-validated keyword
    BEE_CANDIDATE = "BEE_CANDIDATE"   # BEE with unvalidated candidate keyword
    AUTO_KEYWORD = "AUTO_KEYWORD"      # Auto-generated keyword from phrase


# ---------------------------------------------------------------------------
# Promotion Decision (adapter-level gate)
# ---------------------------------------------------------------------------

class PromotionDecision(str, Enum):
    PROMOTE = "PROMOTE"
    KEEP_EVIDENCE_ONLY = "KEEP_EVIDENCE_ONLY"
    DROP = "DROP"
    QUARANTINE = "QUARANTINE"


# ---------------------------------------------------------------------------
# BEE Target Attribution (review target attribution gate)
# ---------------------------------------------------------------------------

class AttributionSource(str, Enum):
    """How a BEE phrase was attributed to the review target product."""
    DIRECT_REL = "direct_rel"                    # Explicit NER-BEE relation to review target
    PLACEHOLDER_RESOLVED = "placeholder_resolved"  # Subject is placeholder resolved to target
    SAME_ENTITY_RESOLVED = "same_entity_resolved"  # Subject resolved via same_entity merge
    COMPARISON_RESOLVED = "comparison_resolved"    # Target side proven via comparison structure
    UNLINKED = "unlinked"                          # No proof of target attribution


# Priority order: higher = stronger attribution evidence
ATTRIBUTION_PRIORITY = {
    AttributionSource.DIRECT_REL: 4,
    AttributionSource.PLACEHOLDER_RESOLVED: 3,
    AttributionSource.SAME_ENTITY_RESOLVED: 2,
    AttributionSource.COMPARISON_RESOLVED: 1,
    AttributionSource.UNLINKED: 0,
}


# ---------------------------------------------------------------------------
# Keyword Source (dictionary validation status)
# ---------------------------------------------------------------------------

class KeywordSource(str, Enum):
    DICT = "DICT"           # Matched in keyword_surface_map.yaml
    RULE = "RULE"           # Rule-based extraction from BEE phrase
    CANDIDATE = "CANDIDATE" # Unvalidated — quarantine only


# ---------------------------------------------------------------------------
# Fact Status (canonical fact promotion level)
# ---------------------------------------------------------------------------

class FactStatus(str, Enum):
    EVIDENCE_ONLY = "EVIDENCE_ONLY"
    CANONICAL_PROMOTED = "CANONICAL_PROMOTED"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Signal Promotion Status (corpus-level gate)
# ---------------------------------------------------------------------------

class SignalPromotionStatus(str, Enum):
    PROMOTED = "PROMOTED"
    CANDIDATE = "CANDIDATE"
