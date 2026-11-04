"""Собранный фронтенд и API доступны через один web-процесс (spec §8.1)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rapporteur_backend.app import create_app


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<h1>Репортёр</h1>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('rapporteur');", encoding="utf-8")
    return tmp_path


def test_frontend_and_health_share_web(static_dir: Path) -> None:
    client = TestClient(create_app(static_dir))
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Репортёр" in response.text
    assert client.get("/assets/app.js").text == "console.log('rapporteur');"
    assert client.get("/health").json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/api/v1/missing", "/assets/missing.js", "/missing"])
def test_missing_resources_return_404(static_dir: Path, path: str) -> None:
    response = TestClient(create_app(static_dir)).get(path)
    assert response.status_code == 404
    assert "Репортёр" not in response.text


def test_static_directory_from_environment(
    static_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAPPORTEUR_STATIC_DIR", str(static_dir))
    assert TestClient(create_app()).get("/").status_code == 200


def test_incomplete_frontend_build_fails_at_startup(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Не найдена сборка фронтенда"):
        create_app(tmp_path)


def test_backend_without_frontend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAPPORTEUR_STATIC_DIR", raising=False)
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 404
