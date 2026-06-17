"""
P4-1 (Wave 3.1): `/api/quarantine/entries` input validation.

Allowed `table` values must mirror `src/qa/quarantine_handler.py` outputs.
Unknown values → 400. page/size bounds checked.
"""

from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from src.web import server


def _make_client_with_state(quarantine_entries: list[dict]) -> TestClient:
    server.demo_state.loaded = True
    server.demo_state.quarantine_entries = quarantine_entries
    server.demo_state.quarantine_stats = {}
    return TestClient(server.app)


def test_allowed_table_value_passes() -> None:
    client = _make_client_with_state([
        {"table": "quarantine_product_match", "review_id": "r1"},
        {"table": "quarantine_placeholder", "review_id": "r2"},
    ])
    response = client.get("/api/quarantine/entries", params={"table": "quarantine_product_match"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["review_id"] == "r1"


def test_unknown_table_value_rejected() -> None:
    client = _make_client_with_state([])
    response = client.get(
        "/api/quarantine/entries",
        params={"table": "users; DROP TABLE quarantine_projection_miss; --"},
    )
    assert response.status_code == 400
    assert "Invalid table" in response.json()["detail"]


def test_empty_table_returns_all_entries() -> None:
    client = _make_client_with_state([
        {"table": "quarantine_product_match", "review_id": "r1"},
        {"table": "quarantine_placeholder", "review_id": "r2"},
    ])
    response = client.get("/api/quarantine/entries")
    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_page_must_be_positive() -> None:
    client = _make_client_with_state([])
    response = client.get("/api/quarantine/entries", params={"page": 0})
    assert response.status_code == 400


def test_size_must_be_in_range() -> None:
    client = _make_client_with_state([])
    for bad in (0, 201, -5):
        response = client.get("/api/quarantine/entries", params={"size": bad})
        assert response.status_code == 400, f"size={bad} should be rejected"


def test_whitelist_matches_quarantine_handler_emissions() -> None:
    """Contract: the whitelist enum in server.py must include every `table` name
    emitted by QuarantineHandler. If a new quarantine table is added without
    extending the whitelist, this test fails.
    """
    from src.qa import quarantine_handler

    emitted = set()
    src = inspect.getsource(quarantine_handler)
    for line in src.splitlines():
        line = line.strip()
        if line.startswith('table="quarantine_'):
            # extract the literal between the surrounding quotes
            value = line.split('"')[1]
            emitted.add(value)

    assert emitted, "No quarantine table literals found in handler source"
    missing = emitted - server._ALLOWED_QUARANTINE_TABLES
    assert not missing, (
        f"Quarantine handler emits tables not in the API whitelist: {missing}. "
        f"Update _ALLOWED_QUARANTINE_TABLES in src/web/server.py."
    )
