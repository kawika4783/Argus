import os
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
