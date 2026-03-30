"""
User repository: user_master + user_summary_raw + purchase_event_raw.
"""

from __future__ import annotations

import json
from typing import Any

from src.db.unit_of_work import UnitOfWork


async def upsert_user_master(uow: UnitOfWork, user: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO user_master (user_id, age, age_band, gender, skin_type, skin_tone,
            raw_payload, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (user_id) DO UPDATE SET
            age=EXCLUDED.age, age_band=EXCLUDED.age_band,
            gender=EXCLUDED.gender, skin_type=EXCLUDED.skin_type,
            skin_tone=EXCLUDED.skin_tone, updated_at=EXCLUDED.updated_at
    """,
        user["user_id"], user.get("age"), user.get("age_band"),
        user.get("gender"), user.get("skin_type"), user.get("skin_tone"),
        json.dumps(user.get("raw_payload")) if user.get("raw_payload") else None,
        uow.as_of_ts,
    )


async def upsert_user_summary(uow: UnitOfWork, summary: dict[str, Any]) -> None:
    await uow.execute("""
        INSERT INTO user_summary_raw (user_id, purchase_summary, chat_summary, updated_at)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (user_id) DO UPDATE SET
            purchase_summary=EXCLUDED.purchase_summary,
            chat_summary=EXCLUDED.chat_summary,
            updated_at=EXCLUDED.updated_at
    """,
        summary["user_id"],
        json.dumps(summary.get("purchase_summary")) if summary.get("purchase_summary") else None,
        json.dumps(summary.get("chat_summary")) if summary.get("chat_summary") else None,
        uow.as_of_ts,
    )


async def insert_purchase_events(uow: UnitOfWork, events: list[dict]) -> None:
    for ev in events:
        await uow.execute("""
            INSERT INTO purchase_event_raw (purchase_event_id, user_id, product_id,
                purchased_at, price, quantity, channel)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (purchase_event_id) DO NOTHING
        """,
            ev["purchase_event_id"], ev["user_id"], ev["product_id"],
            ev.get("purchased_at"), ev.get("price"), ev.get("quantity", 1),
            ev.get("channel"),
        )
