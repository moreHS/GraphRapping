"""
Projection Registry: deterministic mapping from canonical facts to serving signals.

Rule: 1 input combination → 1 deterministic action.
Unmapped combos → explicit DROP / QUARANTINE / KEEP_CANONICAL_ONLY.

Registry loaded from configs/projection_registry.csv (14-column format).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.config_loader import load_csv


@dataclass(frozen=True)
class ProjectionKey:
    """Input key for registry lookup."""
    input_predicate: str
    subject_type: str
    object_type: str
    polarity: str  # POS|NEG|NEU|MIXED|'' (empty = any)


@dataclass
class ProjectionRule:
    """Output rule from registry."""
    registry_version: str
    input_predicate: str
    subject_type: str
    object_type: str
    polarity: str
    qualifier_required: bool
    qualifier_type: str
    output_signal_family: str
    output_edge_type: str
    output_dst_type: str
    output_transform: str
    output_weight_rule: str
    if_unresolved_action: str  # DROP|QUARANTINE|KEEP_CANONICAL_ONLY
    notes: str
    # Phase 1 additions (optional columns, defaults preserve backward compatibility)
    allowed_evidence_kind: str = ""      # Empty = any; comma-separated if multiple
    min_confidence: float = 0.0          # Minimum fact confidence for this rule
    promotion_mode: str = "IMMEDIATE"    # IMMEDIATE|CORPUS_THRESHOLD|NEVER


@dataclass
class ProjectionResult:
    """Result of projecting a canonical fact."""
    signal_family: str
    edge_type: str
    dst_type: str
    transform: str
    weight_rule: str
    registry_version: str
    qualifier_required: bool = False
    qualifier_type: str = ""


class ProjectionRegistry:
    """Deterministic projection registry.

    Loaded from CSV, provides lookup by (predicate, subj_type, obj_type, polarity).
    """

    def __init__(self) -> None:
        self._rules: dict[ProjectionKey, ProjectionRule] = {}
        self._version: str = ""

    def load(self, csv_filename: str = "projection_registry.csv") -> None:
        rows = load_csv(csv_filename)
        self._rules.clear()
        for row in rows:
            key = ProjectionKey(
                input_predicate=row.get("input_predicate", "").strip(),
                subject_type=row.get("subject_type", "").strip(),
                object_type=row.get("object_type", "").strip(),
                polarity=row.get("polarity", "").strip(),
            )
            # Parse min_confidence safely
            min_conf_str = row.get("min_confidence", "").strip()
            min_conf = float(min_conf_str) if min_conf_str else 0.0

            rule = ProjectionRule(
                registry_version=row.get("registry_version", "").strip(),
                input_predicate=key.input_predicate,
                subject_type=key.subject_type,
                object_type=key.object_type,
                polarity=key.polarity,
                qualifier_required=row.get("qualifier_required", "").strip().upper() == "Y",
                qualifier_type=row.get("qualifier_type", "").strip(),
                output_signal_family=row.get("output_signal_family", "").strip(),
                output_edge_type=row.get("output_edge_type", "").strip(),
                output_dst_type=row.get("output_dst_type", "").strip(),
                output_transform=row.get("output_transform", "").strip(),
                output_weight_rule=row.get("output_weight_rule", "").strip(),
                if_unresolved_action=row.get("if_unresolved_action", "QUARANTINE").strip(),
                notes=row.get("notes", "").strip(),
                allowed_evidence_kind=row.get("allowed_evidence_kind", "").strip(),
                min_confidence=min_conf,
                promotion_mode=row.get("promotion_mode", "IMMEDIATE").strip() or "IMMEDIATE",
            )
            self._rules[key] = rule
            if rule.registry_version:
                self._version = rule.registry_version

    @property
    def version(self) -> str:
        return self._version

    def lookup(
        self,
        predicate: str,
        subject_type: str,
        object_type: str,
        polarity: str = "",
    ) -> ProjectionRule | None:
        """Lookup projection rule for a canonical fact.

        Tries exact match first, then fallback with empty polarity.
        Returns None if no mapping exists.
        """
        # Exact match
        key = ProjectionKey(predicate, subject_type, object_type, polarity)
        if key in self._rules:
            return self._rules[key]

        # Fallback: any polarity
        if polarity:
            key_any = ProjectionKey(predicate, subject_type, object_type, "")
            if key_any in self._rules:
                return self._rules[key_any]

        return None

    def project(
        self,
        predicate: str,
        subject_type: str,
        object_type: str,
        polarity: str = "",
    ) -> ProjectionResult | str:
        """Project a canonical fact to a serving signal.

        Returns:
            ProjectionResult on success
            str action on failure: 'DROP' | 'QUARANTINE' | 'KEEP_CANONICAL_ONLY'
        """
        rule = self.lookup(predicate, subject_type, object_type, polarity)
        if rule is None:
            return "QUARANTINE"

        action = rule.if_unresolved_action
        if action in ("DROP", "QUARANTINE", "KEEP_CANONICAL_ONLY"):
            if not rule.output_signal_family:
                return action

        return ProjectionResult(
            signal_family=rule.output_signal_family,
            edge_type=rule.output_edge_type,
            dst_type=rule.output_dst_type,
            transform=rule.output_transform,
            weight_rule=rule.output_weight_rule,
            registry_version=rule.registry_version,
            qualifier_required=rule.qualifier_required,
            qualifier_type=rule.qualifier_type,
        )

    @property
    def predicates(self) -> set[str]:
        return {k.input_predicate for k in self._rules}

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def completeness_check(self, observed_combos: list[tuple[str, str, str, str]]) -> list[tuple]:
        """Check which observed combos have no registry mapping.

        Args:
            observed_combos: list of (predicate, subj_type, obj_type, polarity)

        Returns:
            list of unmapped combos
        """
        unmapped = []
        for combo in observed_combos:
            pred, subj, obj, pol = combo
            if self.lookup(pred, subj, obj, pol) is None:
                unmapped.append(combo)
        return unmapped
