from fastapi.testclient import TestClient
from itr_api import main

client = TestClient(main.app)


def test_health_without_table(monkeypatch):
    monkeypatch.delenv("TABLE_NAME", raising=False)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": main.VERSION, "db": "skipped"}


def test_health_reports_database_error(monkeypatch):
    class BrokenTable:
        def get_item(self, **_kwargs):
            raise RuntimeError("no connection")

    monkeypatch.setenv("TABLE_NAME", "itr-test")
    monkeypatch.setattr(main, "_get_table", lambda: BrokenTable())
    response = client.get("/api/health")
    assert response.json()["status"] == "degraded"
    assert response.json()["db"] == "error"


def test_unknown_route_is_404():
    assert client.get("/api/nope").status_code == 404
