"""Выбор модели и режима рассуждения на странице «Управление». Ollama — локальный HTTP-сервер
с ответом /api/tags, как у Ollama 0.40."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from rapporteur import settings
from rapporteur.app import create_app
from rapporteur.contracts import ModelProfile
from rapporteur.llm.client import ScriptedClient

from .test_app import Fixture, processed_talk


class FakeOllama(BaseHTTPRequestHandler):
    models: ClassVar[list[dict[str, Any]]] = [
        {
            "name": "qwen3.5:9b",
            "size": 6_600_000_000,
            "digest": "a" * 64,
            "details": {"parameter_size": "9.7B", "quantization_level": "Q4_K_M"},
            "capabilities": ["completion", "vision", "tools", "thinking"],
        },
        {
            "name": "qwen2.5:7b",
            "size": 4_700_000_000,
            "digest": "b" * 64,
            "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M"},
            "capabilities": ["completion", "tools"],
        },
    ]

    def do_GET(self) -> None:
        body = json.dumps({"models": self.models}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(settings, "OLLAMA_URL", url)
    yield url
    server.shutdown()


@pytest.fixture
def used() -> list[ModelProfile]:
    return []


@pytest.fixture
def app(tmp_path: Path, ollama: str, used: list[ModelProfile]) -> Iterator[Fixture]:
    scripted = ScriptedClient([])

    def client(profile: ModelProfile) -> ScriptedClient:
        used.append(profile)
        return scripted

    with TestClient(create_app(tmp_path, client=client, process=False)) as test_client:
        yield Fixture(test_client, scripted, tmp_path)


def test_default_model_comes_from_profiles_yaml(app: Fixture) -> None:
    state = app.client.get("/api/v1/model").json()

    assert state["current"] == {
        "profile": "local",
        "model": "qwen3.5:9b",
        "reasoning": True,
        "chosen": False,
    }
    assert state["ollama_available"] is True
    assert {item["name"]: item["thinking"] for item in state["models"]} == {
        "qwen3.5:9b": True,
        "qwen2.5:7b": False,
    }


def test_choice_is_validated_saved_and_reset(app: Fixture) -> None:
    thinking = app.client.put("/api/v1/model", json={"model": "qwen2.5:7b", "reasoning": True})
    assert thinking.status_code == 422 and "рассуждения" in thinking.json()["message"]
    unknown = app.client.put("/api/v1/model", json={"model": "llama:70b", "reasoning": False})
    assert unknown.status_code == 422

    saved = app.client.put("/api/v1/model", json={"model": "qwen2.5:7b", "reasoning": False})
    assert saved.status_code == 200, saved.text
    assert saved.json()["current"] == {
        "profile": "qwen2.5:7b",
        "model": "qwen2.5:7b",
        "reasoning": False,
        "chosen": True,
    }
    assert app.client.get("/api/v1/model").json()["current"]["chosen"] is True

    reset = app.client.put("/api/v1/model", json={"model": None})
    assert reset.json()["current"]["model"] == "qwen3.5:9b"
    assert reset.json()["current"]["chosen"] is False


def test_processing_uses_the_chosen_model(app: Fixture, used: list[ModelProfile]) -> None:
    app.client.put("/api/v1/model", json={"model": "qwen3.5:9b", "reasoning": False})

    _, talk_id = processed_talk(app)

    assert {(profile.model, profile.generation.reasoning) for profile in used} == {
        ("qwen3.5:9b", False)
    }
    assert used[0].weights_revision == "sha256:" + "a" * 64
    assert app.scripted.requests[0].think is False
    summary = app.client.get(f"/api/v1/talks/{talk_id}/summary").json()["summary"]
    assert summary["profile"] == "qwen3.5:9b"


def test_choice_needs_ollama(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OLLAMA_URL", "http://127.0.0.1:9")
    with TestClient(create_app(tmp_path, process=False)) as client:
        assert client.get("/api/v1/model").json()["ollama_available"] is False
        saved = client.put("/api/v1/model", json={"model": "qwen3.5:9b", "reasoning": True})

    assert saved.status_code == 503 and "Ollama недоступна" in saved.json()["message"]
