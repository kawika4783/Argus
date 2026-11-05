import os
import httpx
os.environ["NEXUSINTEL_DATABASE_URL"] = "sqlite:///./test-nexusintel.db"
os.environ["NEXUSINTEL_DEMO_PASSWORD"] = "demo-pass"

from fastapi.testclient import TestClient
from apps.api.app.main import app

client = TestClient(app)

def test_health():
    assert client.get("/health").json()["status"] == "ok"

def test_login_dashboard_and_logout():
    assert client.get("/dashboard").status_code == 401
    login = client.post("/auth/login", json={"username": "analyst", "password": "demo-pass"})
    assert login.status_code == 200
    assert client.get("/dashboard").status_code == 200
    assert client.get("/auth/me").json()["role"] == "analyst"
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/dashboard").status_code == 401

def test_authorized_case_and_entity_creation():
    client.post("/auth/login", json={"username": "analyst", "password": "demo-pass"})
    created_case = client.post("/cases", json={"title": "Synthetic follow-up", "description": "Created by the authorized analyst.", "priority": "medium"})
    assert created_case.status_code == 200
    case_id = created_case.json()["id"]
    created_entity = client.post("/entities", json={"name": "Synthetic Contact", "entity_type": "Person", "description": "Synthetic record", "confidence": 65, "case_id": case_id})
    assert created_entity.status_code == 200
    assert created_entity.json()["case_id"] == case_id

def test_entity_creation_cannot_cross_case_boundary():
    client.post("/auth/login", json={"username": "analyst", "password": "demo-pass"})
    response = client.post("/entities", json={"name": "Restricted", "entity_type": "Person", "case_id": "CS-NOT-AUTHORIZED"})
    assert response.status_code == 404

def test_relationship_creation_is_case_scoped():
    client.post("/auth/login", json={"username": "analyst", "password": "demo-pass"})
    created_case = client.post("/cases", json={"title": "Relationship test", "description": "Synthetic relationship case.", "priority": "medium"}).json()
    case_id = created_case["id"]
    first = client.post("/entities", json={"name": "Source entity", "entity_type": "Person", "case_id": case_id}).json()
    second = client.post("/entities", json={"name": "Target entity", "entity_type": "Organization", "case_id": case_id}).json()
    created = client.post("/relationships", json={"source_id": first["id"], "target_id": second["id"], "relationship_type": "Associated With", "confidence": 82, "case_id": case_id})
    assert created.status_code == 200
    assert created.json()["case_id"] == case_id
    assert len(client.get(f"/relationships?case_id={case_id}").json()) == 1
    assert client.post("/relationships", json={"source_id": first["id"], "target_id": "E-18472", "relationship_type": "Cross Case", "case_id": case_id}).status_code == 404

def test_zero_cost_ai_status_and_deterministic_question():
    client.post("/auth/login", json={"username": "analyst", "password": "demo-pass"})
    status_response = client.get("/ai/status")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["zero_cost_mode"] is True
    assert status_body["openai"]["available"] is False
    question = client.post("/ai/question", json={"question": "Estimate this scenario", "analysis_type": "monte_carlo", "probability": 0.4, "trials": 500})
    assert question.status_code == 200
    assert question.json()["engine"] == "deterministic"
    assert question.json()["ai_generated"] is False
    stream = client.post("/ai/question/stream", json={"question": "Stream this deterministic estimate", "analysis_type": "monte_carlo", "probability": 0.4, "trials": 500})
    assert stream.status_code == 200
    assert "[DONE]" in stream.text
    assert question.json()["metrics"]["trials"] == 500
    assert client.get("/admin/ai/settings").status_code == 403

def test_news_sync_is_deduplicated_and_case_attachable(monkeypatch):
    client.post("/auth/login", json={"username": "analyst", "password": "demo-pass"})
    feed = b"""<?xml version='1.0'?><rss version='2.0'><channel><item><guid>demo-1</guid><title>First synthetic headline</title><link>https://example.com/first</link><description>First summary</description><pubDate>Wed, 04 Nov 2026 12:00:00 GMT</pubDate></item><item><guid>demo-2</guid><title>Second synthetic headline</title><link>https://example.com/second</link><description>Second summary</description></item></channel></rss>"""

    class FakeResponse:
        content = feed

        def raise_for_status(self):
            return None

    original_get = httpx.Client.get

    def fake_get(self, url, **kwargs):
        if "gdelt" in str(url):
            return FakeResponse()
        return original_get(self, url, **kwargs)

    monkeypatch.setattr("apps.api.app.main.httpx.Client.get", fake_get)
    first_sync = client.post("/news/sources/SRC-GDELT/sync")
    assert first_sync.status_code == 200
    assert first_sync.json()["items_seen"] == 2
    second_sync = client.post("/news/sources/SRC-GDELT/sync")
    assert second_sync.status_code == 200
    assert second_sync.json()["items_created"] == 0
    assert second_sync.json()["items_skipped"] == 2
    items = client.get("/news/items").json()
    demo_item = next(item for item in items if item["external_id"] == "demo-1")
    attached = client.post(f"/news/items/{demo_item['id']}/attach", json={"case_id": "CS-4729"})
    assert attached.status_code == 200
    assert attached.json()["review_status"] == "attached"
