"""Input connector contracts (IC-0 / plan 2026-07-19 §2).

Pure, side-effect-free validators for the four raw sources GraphRapping loads
from the real DB pipeline, plus the RS↔Relation field-mapping table that is the
single source of truth for the documentation-vs-fixture field-name difference.

Each ``validate_*`` returns a ``list[str]`` of human-readable violation reasons;
an empty list means the record satisfies the contract. Collection-level
``validate_records`` aggregates pass/violation counts and the top violation
reasons WITHOUT retaining any record payload (only keys/indices), so a report
can be embedded in a git-tracked staging manifest without leaking review text
or PII.

Contract separation (codex #1): the raw ``rs.jsonl`` S3 output and the current
``relation`` landing JSON are DIFFERENT shapes for the same information, so they
have distinct contracts. The 9-digit ``REPRESENTATIVE_PROD_CODE`` rule is NEVER
a catalog rejection reason (codex #4: the golden catalog itself carries 6
non-conforming rep codes and must still pass); joinability is reported
separately by :func:`report_rep_code_joinability`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# RS(raw rs.jsonl) ↔ Relation(landing) field mapping — single source of truth
# ---------------------------------------------------------------------------
#
# Verified field-by-field against src/loaders/rs_jsonl_loader._convert_rs_record
# (the code that actually performs the mapping) and mockdata/SCHEMA_RS_JSONL.md
# §11. Each value is the Relation-landing field the RS field maps to. Where the
# loader derives or nests the target, the note after "→" documents it.
RS_TO_RELATION_FIELD_MAP: dict[str, str] = {
    "id": "source_review_key",
    # rs_jsonl_loader: created_at=record["date"]; relation_loader renames
    # drup_dt→created_at, so date and drup_dt meet at created_at.
    "date": "drup_dt",
    "product_id": "source_product_id",
    "prd_nm": "prod_nm",
    # rs_jsonl_loader keeps channel as source_channel AND derives clct_site_nm
    # via the channel→site map.
    "channel": "source_channel",
    "brnd_nm": "brnd_nm",
    "prd_apal_scr": "source_rating",
    "ner_spans": "ner",
    "bee_spans": "bee",
    "relation": "relation",
    "text": "text",
    # own-source top-level demographics collapse into the nested reviewer_profile
    # (rs_jsonl_loader builds author_key from age_sctn_cd+sex_cd; the relation
    # landing nests all four under reviewer_profile).
    "age_sctn_cd": "reviewer_profile.age_sctn_cd",
    "sex_cd": "reviewer_profile.sex_cd",
    "sktp_nm": "reviewer_profile.sktp_nm",
    "sktr_nm": "reviewer_profile.sktr_nm",
}

# Own-source demographic fields (top-level in rs.jsonl, nested in relation).
_DEMOGRAPHIC_FIELDS = ("age_sctn_cd", "sex_cd", "sktp_nm", "sktr_nm")

# 9-digit numeric representative-code rule (mirrors
# scripts/fetch_user_profiles_pg.REP_CODE_RE). NOT a catalog rejection rule —
# only a purchase-join observability signal (see report_rep_code_joinability).
REP_CODE_RE = re.compile(r"^[0-9]{9}$")

# Product-catalog identity: the 3-key source identity plus the serving product id.
PRODUCT_IDENTITY_KEYS = (
    "SOURCE_CHANNEL",
    "SOURCE_KEY_TYPE",
    "SOURCE_PRODUCT_ID",
    "ONLINE_PROD_SERIAL_NUMBER",
)

# Contract kinds accepted by validate_records.
ContractKind = str  # one of _VALIDATORS below


# ---------------------------------------------------------------------------
# Low-level structural helpers
# ---------------------------------------------------------------------------

def _check_span_list(value: Any, key: str) -> list[str]:
    """Structural check for a span/relation list: a list of objects whose
    ``label``/``entity_group`` (when present) is a string."""
    if not isinstance(value, list):
        return [f"field {key} must be a list"]
    reasons: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, Mapping):
            reasons.append(f"{key}[{idx}] must be an object")
            continue
        label = item.get("label", item.get("entity_group"))
        if label is not None and not isinstance(label, str):
            reasons.append(f"{key}[{idx}].label must be a string")
    return reasons


def _check_identifier(record: Mapping[str, Any], key: str, reasons: list[str]) -> None:
    """Required identifier: present, non-null, non-empty, str or int."""
    if key not in record:
        reasons.append(f"missing required field: {key}")
        return
    value = record.get(key)
    if value is None or str(value).strip() == "":
        reasons.append(f"field {key} must be a non-empty identifier")
    elif not isinstance(value, (str, int)) or isinstance(value, bool):
        reasons.append(f"field {key} must be str/int, got {type(value).__name__}")


def _check_required_str(
    record: Mapping[str, Any], key: str, reasons: list[str], *, allow_empty: bool = True
) -> None:
    if key not in record:
        reasons.append(f"missing required field: {key}")
        return
    value = record.get(key)
    if not isinstance(value, str):
        reasons.append(f"field {key} must be str, got {type(value).__name__}")
    elif not allow_empty and value.strip() == "":
        reasons.append(f"field {key} must be non-empty")


# ---------------------------------------------------------------------------
# Contract 1: raw rs.jsonl source (S3 operational output)
# ---------------------------------------------------------------------------

def validate_rs_jsonl_record(record: Any) -> list[str]:
    """Validate one raw ``rs.jsonl`` record against SCHEMA_RS_JSONL.md.

    Required: ``id``/``text``/``date`` (str), ``channel`` (non-empty str),
    ``product_id`` (non-empty identifier). ``ner_spans``/``bee_spans`` are lists
    of objects. Additional-planned fields (``brnd_nm``/``relation``/
    ``prd_apal_scr``) and top-level demographics are nullable/optional.
    """
    if not isinstance(record, Mapping):
        return ["record is not an object"]
    reasons: list[str] = []
    _check_required_str(record, "id", reasons, allow_empty=False)
    _check_required_str(record, "text", reasons)
    _check_required_str(record, "date", reasons)
    _check_required_str(record, "channel", reasons, allow_empty=False)
    _check_identifier(record, "product_id", reasons)

    for key in ("ner_spans", "bee_spans"):
        if key in record:
            reasons.extend(_check_span_list(record.get(key), key))

    # Additional-planned nullable fields.
    relation = record.get("relation")
    if relation is not None and not isinstance(relation, list):
        reasons.append("field relation must be a list or null")
    brnd_nm = record.get("brnd_nm")
    if brnd_nm is not None and not isinstance(brnd_nm, str):
        reasons.append("field brnd_nm must be str or null")
    scr = record.get("prd_apal_scr")
    if scr is not None and (isinstance(scr, bool) or not isinstance(scr, (int, float))):
        reasons.append("field prd_apal_scr must be a number or null")

    # Optional top-level demographics.
    for key in _DEMOGRAPHIC_FIELDS:
        value = record.get(key)
        if value is not None and not isinstance(value, str):
            reasons.append(f"field {key} must be str or null")
    return reasons


# ---------------------------------------------------------------------------
# Contract 2: relation landing JSON (current fixture / pipeline input)
# ---------------------------------------------------------------------------

def validate_relation_landing_record(record: Any) -> list[str]:
    """Validate one relation-landing record (current ``review_triples_raw`` shape).

    Required: ``source_review_key``/``channel`` (non-empty str), ``drup_dt``/
    ``text`` (str), ``source_product_id`` (non-empty identifier), and
    ``ner``/``bee``/``relation`` lists. ``reviewer_profile`` is an optional
    nested object; ``brnd_nm`` is nullable.
    """
    if not isinstance(record, Mapping):
        return ["record is not an object"]
    reasons: list[str] = []
    _check_required_str(record, "source_review_key", reasons, allow_empty=False)
    _check_required_str(record, "drup_dt", reasons)
    _check_required_str(record, "channel", reasons, allow_empty=False)
    _check_required_str(record, "text", reasons)
    _check_identifier(record, "source_product_id", reasons)

    for key in ("ner", "bee", "relation"):
        if key not in record:
            reasons.append(f"missing required field: {key}")
        else:
            reasons.extend(_check_span_list(record.get(key), key))

    reviewer_profile = record.get("reviewer_profile")
    if reviewer_profile is not None:
        if not isinstance(reviewer_profile, Mapping):
            reasons.append("field reviewer_profile must be an object or null")
        else:
            for key in _DEMOGRAPHIC_FIELDS:
                value = reviewer_profile.get(key)
                if value is not None and not isinstance(value, str):
                    reasons.append(f"reviewer_profile.{key} must be str or null")

    brnd_nm = record.get("brnd_nm")
    if brnd_nm is not None and not isinstance(brnd_nm, str):
        reasons.append("field brnd_nm must be str or null")
    return reasons


# ---------------------------------------------------------------------------
# Contract 3: product catalog (ES-compatible master snapshot)
# ---------------------------------------------------------------------------

def validate_product_catalog_record(record: Any) -> list[str]:
    """Validate one product-catalog record.

    Required: the 3-key source identity (``SOURCE_CHANNEL``/``SOURCE_KEY_TYPE``/
    ``SOURCE_PRODUCT_ID``) plus ``ONLINE_PROD_SERIAL_NUMBER``. The collision
    marker ``SOURCE_COMPAT_COLLAPSED`` is allowed (bool when present).
    ``REPRESENTATIVE_PROD_CODE`` is intentionally NOT rejected for a non-9-digit
    value (codex #4) — see :func:`report_rep_code_joinability`.
    """
    if not isinstance(record, Mapping):
        return ["record is not an object"]
    reasons: list[str] = []
    for key in PRODUCT_IDENTITY_KEYS:
        if key not in record:
            reasons.append(f"missing required field: {key}")
        else:
            value = record.get(key)
            if value is None or str(value).strip() == "":
                reasons.append(f"field {key} must be a non-empty identifier")

    marker = record.get("SOURCE_COMPAT_COLLAPSED")
    if "SOURCE_COMPAT_COLLAPSED" in record and not isinstance(marker, bool):
        reasons.append("field SOURCE_COMPAT_COLLAPSED must be a boolean when present")
    return reasons


# ---------------------------------------------------------------------------
# Contract 4: user profile (normalized 3-group + optional purchase_events)
# ---------------------------------------------------------------------------

def validate_user_profile(profile: Any) -> list[str]:
    """Validate one normalized user profile.

    Required: ``basic`` (object). Rejects the raw 7-column shape (mirrors
    ``user_loader``'s guard). ``purchase_analysis``/``chat`` are optional objects
    (``chat`` may be null). Optional ``purchase_events`` must be a list of
    objects each carrying a usable ``product_id`` (and, when present, a positive
    integer ``quantity``) — matching ``extract_purchase_events_from_profiles``.
    """
    if not isinstance(profile, Mapping):
        return ["profile is not an object"]
    reasons: list[str] = []
    if "user_profile" in profile or "skin_profile" in profile:
        reasons.append(
            "profile appears to be raw 7-column format; expected normalized "
            "{basic, purchase_analysis, chat}"
        )
    if "basic" not in profile:
        reasons.append("missing required key: basic")
    elif not isinstance(profile.get("basic"), Mapping):
        reasons.append("field basic must be an object")

    pa = profile.get("purchase_analysis")
    if "purchase_analysis" in profile and pa is not None and not isinstance(pa, Mapping):
        reasons.append("field purchase_analysis must be an object or null")
    chat = profile.get("chat")
    if "chat" in profile and chat is not None and not isinstance(chat, Mapping):
        reasons.append("field chat must be an object or null")

    if "purchase_events" in profile:
        events = profile.get("purchase_events")
        if not isinstance(events, list):
            reasons.append("field purchase_events must be a list when present")
        else:
            for idx, ev in enumerate(events):
                if not isinstance(ev, Mapping):
                    reasons.append(f"purchase_events[{idx}] must be an object")
                    continue
                pid = ev.get("product_id")
                if pid is None or str(pid).strip() == "":
                    reasons.append(f"purchase_events[{idx}] missing product_id")
                quantity = ev.get("quantity")
                if quantity is not None and (
                    isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0
                ):
                    reasons.append(
                        f"purchase_events[{idx}].quantity must be a positive int when present"
                    )
    return reasons


_VALIDATORS: dict[str, Any] = {
    "rs_jsonl": validate_rs_jsonl_record,
    "relation": validate_relation_landing_record,
    "product_catalog": validate_product_catalog_record,
    "user_profile": validate_user_profile,
}


# ---------------------------------------------------------------------------
# Collection-level report (aggregate only — no payload retained)
# ---------------------------------------------------------------------------

@dataclass
class ValidationReport:
    """Aggregate outcome of validating a collection. Never holds record payload
    (only keys/indices), so it is safe to embed in a git-tracked manifest."""

    kind: str
    total: int = 0
    passed: int = 0
    violations: int = 0
    violations_top: list[tuple[str, int]] = field(default_factory=list)
    violation_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.violations == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "total": self.total,
            "passed": self.passed,
            "violations": self.violations,
            "violations_top": [
                {"reason": reason, "count": count} for reason, count in self.violations_top
            ],
            "violation_keys": list(self.violation_keys),
        }

    def to_manifest_dict(self) -> dict[str, Any]:
        """Compact form for a staging manifest ``validation`` block."""
        return {
            "passed": self.passed,
            "total": self.total,
            "violations": self.violations,
            "violations_top": [
                {"reason": reason, "count": count} for reason, count in self.violations_top
            ],
        }


def _record_key(kind: str, record: Any, index: int) -> str:
    if isinstance(record, Mapping):
        if kind == "rs_jsonl":
            return str(record.get("id") or f"#{index}")
        if kind == "relation":
            return str(record.get("source_review_key") or f"#{index}")
        if kind == "product_catalog":
            return str(
                record.get("SOURCE_IDENTITY_KEY")
                or record.get("SOURCE_PRODUCT_ID")
                or f"#{index}"
            )
    return f"#{index}"


def validate_records(
    records: Any,
    kind: str,
    *,
    top_n: int = 10,
    max_keys: int = 50,
) -> ValidationReport:
    """Validate a collection and return an aggregate :class:`ValidationReport`.

    ``records`` is a sequence of records, or (for ``user_profile``) a mapping of
    ``{user_id: profile}``; the mapping key becomes the violation key. Only the
    top ``top_n`` violation reasons and the first ``max_keys`` failing keys are
    retained — never any record content.
    """
    validator = _VALIDATORS.get(kind)
    if validator is None:
        raise ValueError(
            f"unknown contract kind: {kind!r} (expected one of {sorted(_VALIDATORS)})"
        )
    is_mapping = isinstance(records, Mapping)
    items = list(records.items()) if is_mapping else list(enumerate(records))
    report = ValidationReport(kind=kind, total=len(items))
    reason_counts: dict[str, int] = {}
    for key_or_index, record in items:
        reasons = validator(record)
        if not reasons:
            report.passed += 1
            continue
        report.violations += 1
        rec_key = str(key_or_index) if is_mapping else _record_key(kind, record, key_or_index)
        if len(report.violation_keys) < max_keys:
            report.violation_keys.append(rec_key)
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    report.violations_top = sorted(
        reason_counts.items(), key=lambda kv: (-kv[1], kv[0])
    )[:top_n]
    return report


# ---------------------------------------------------------------------------
# Product joinability observability (9-digit REP_CODE — reported, never rejected)
# ---------------------------------------------------------------------------

@dataclass
class RepCodeJoinabilityReport:
    """Purchase-join readiness of a product catalog by REPRESENTATIVE_PROD_CODE."""

    total: int = 0
    joinable_9digit: int = 0
    nonconforming: int = 0
    missing: int = 0
    nonconforming_reason_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "joinable_9digit": self.joinable_9digit,
            "nonconforming": self.nonconforming,
            "missing": self.missing,
            "nonconforming_reason_counts": dict(self.nonconforming_reason_counts),
        }


def report_rep_code_joinability(records: Any) -> RepCodeJoinabilityReport:
    """Aggregate how many catalog records carry a 9-digit REPRESENTATIVE_PROD_CODE
    (purchase-joinable) vs. a non-conforming one (Z_Z markers, wrong length, or
    missing/null). Aggregate counts only — no product ids surfaced."""
    report = RepCodeJoinabilityReport()
    for record in records:
        report.total += 1
        rep = record.get("REPRESENTATIVE_PROD_CODE") if isinstance(record, Mapping) else None
        rep_s = str(rep).strip() if rep is not None else ""
        if rep_s == "":
            report.nonconforming += 1
            report.missing += 1
            report.nonconforming_reason_counts["missing_or_empty"] = (
                report.nonconforming_reason_counts.get("missing_or_empty", 0) + 1
            )
        elif REP_CODE_RE.match(rep_s):
            report.joinable_9digit += 1
        else:
            report.nonconforming += 1
            reason = "non_numeric" if not rep_s.isdigit() else "wrong_length"
            report.nonconforming_reason_counts[reason] = (
                report.nonconforming_reason_counts.get(reason, 0) + 1
            )
    return report
