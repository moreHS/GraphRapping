"""
User master + summary ingest.

Loads user data into user_master and user_summary_raw tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class UserRecord:
    user_id: str
    age: int | None = None
    age_band: str | None = None
    gender: str | None = None
    skin_type: str | None = None
    skin_tone: str | None = None
    raw_payload: dict | None = None


@dataclass
class UserSummaryRecord:
    user_id: str
    purchase_summary: dict | None = None
    repurchase_summary: dict | None = None
    seasonal_summary: dict | None = None
    chat_summary: dict | None = None


def ingest_user(record: UserRecord) -> dict[str, Any]:
    """Transform user record into user_master row."""
    return {
        "user_id": record.user_id,
        "age": record.age,
        "age_band": record.age_band,
        "gender": record.gender,
        "skin_type": record.skin_type,
        "skin_tone": record.skin_tone,
        "raw_payload": record.raw_payload,
    }


def ingest_user_summary(record: UserSummaryRecord) -> dict[str, Any]:
    """Transform user summary record into user_summary_raw row."""
    return {
        "user_id": record.user_id,
        "purchase_summary": record.purchase_summary,
        "repurchase_summary": record.repurchase_summary,
        "seasonal_summary": record.seasonal_summary,
        "chat_summary": record.chat_summary,
    }
