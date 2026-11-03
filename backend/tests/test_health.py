from importlib.metadata import version

from fastapi.testclient import TestClient

from rapporteur_backend.app import create_app


def test_health_reports_ok_and_version() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": version("rapporteur-backend")}
