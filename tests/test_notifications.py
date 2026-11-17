"""The demo notification inbox the n8n workflow posts to by default."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client() -> TestClient:
    main._notifications.clear()
    return TestClient(main.app)


def test_a_notification_is_accepted_and_listed(client):
    response = client.post("/notifications", json={"text": "Processed: invoice 1"})
    assert response.status_code == 202
    assert response.json() == {"received": True, "held": 1}

    (entry,) = client.get("/notifications").json()["notifications"]
    assert entry["payload"] == {"text": "Processed: invoice 1"}
    assert entry["received_at"]


def test_newest_comes_first_and_only_the_last_fifty_are_kept(client):
    for n in range(55):
        client.post("/notifications", json={"text": f"message {n}"})

    held = client.get("/notifications").json()["notifications"]
    assert len(held) == 50
    assert held[0]["payload"]["text"] == "message 54"
    assert held[-1]["payload"]["text"] == "message 5"


def test_a_body_that_is_not_an_object_is_rejected(client):
    assert client.post("/notifications", json=["not", "an", "object"]).status_code == 422
