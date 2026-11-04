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
