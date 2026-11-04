"""Клиент LLM, ответ по схеме с повторами, промпты и профили (P1-06)."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from rapporteur_contracts import FactsDraft
from rapporteur_pipeline.llm.client import (
    ChatRequest,
    ChatResponse,
    Message,
    OllamaClient,
    ScriptedClient,
)
from rapporteur_pipeline.llm.profiles import PROFILES_FILE, ProfileError, load_profile
from rapporteur_pipeline.llm.prompts import PromptError, load_prompt
from rapporteur_pipeline.llm.structured import ask_structured
from rapporteur_pipeline.stage import FailureCode, StageError

from .fictional_talk import PROFILE, facts_answer

PROMPT = load_prompt("facts", "v1")
MESSAGES = [Message("system", "Факты."), Message("user", "[S1] Текст слайда.")]


class FakeOllama(BaseHTTPRequestHandler):
    status: ClassVar[int] = 200
    reply: ClassVar[dict[str, Any]] = {}
    received: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        type(self).received.append(json.loads(self.rfile.read(length)))
        body = json.dumps(type(self).reply).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def ollama() -> Iterator[str]:
    FakeOllama.received = []
    FakeOllama.status = 200
    server = HTTPServer(("127.0.0.1", 0), FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def request(**changes: Any) -> ChatRequest:
    base: dict[str, Any] = {
        "model": "test-model",
        "messages": tuple(MESSAGES),
        "schema": {"type": "object"},
        "think": True,
        "temperature": 0.1,
        "max_tokens": 2000,
        "context_tokens": 16000,
        "seed": 7,
    }
    return ChatRequest(**{**base, **changes})


def test_ollama_request_carries_schema_reasoning_and_limits(ollama: str) -> None:
    FakeOllama.reply = {
        "message": {"role": "assistant", "content": "{}", "thinking": "думаю"},
        "done_reason": "stop",
        "prompt_eval_count": 120,
        "eval_count": 40,
    }

    response = OllamaClient(ollama).chat(request())

    sent = FakeOllama.received[0]
    assert sent["model"] == "test-model" and sent["stream"] is False
    assert sent["think"] is True and sent["format"] == {"type": "object"}
    assert sent["options"] == {
        "temperature": 0.1,
        "num_ctx": 16000,
        "num_predict": 2000,
        "seed": 7,
    }
    assert sent["messages"][1] == {"role": "user", "content": "[S1] Текст слайда."}
    assert (response.content, response.thinking, response.done_reason) == ("{}", "думаю", "stop")
    assert (response.prompt_tokens, response.completion_tokens) == (120, 40)


def test_ollama_error_is_a_controlled_failure(ollama: str) -> None:
    FakeOllama.status = 404
    FakeOllama.reply = {"error": "model 'test-model' not found"}

    with pytest.raises(StageError) as error:
        OllamaClient(ollama).chat(request())
    assert error.value.code is FailureCode.LLM_UNAVAILABLE
    assert "not found" in error.value.message


def test_unreachable_ollama_is_a_controlled_failure() -> None:
    with pytest.raises(StageError) as error:
        OllamaClient("http://127.0.0.1:9", timeout_s=2).chat(request())
    assert error.value.code is FailureCode.LLM_UNAVAILABLE


def ask(client: ScriptedClient, **kwargs: Any) -> Any:
    return ask_structured(
        client, PROFILE, PROMPT, MESSAGES, FactsDraft, think=True, max_repairs=2, seed=1, **kwargs
    )


def test_invalid_answer_is_repaired_with_the_problems() -> None:
    client = ScriptedClient(["не JSON", facts_answer()])

    result = ask(client)

    assert result.attempts == 2
    repair = client.requests[1].messages[-1]
    assert repair.role == "user" and "ответ не является JSON" in repair.content
    assert client.requests[1].messages[-2] == Message("assistant", "не JSON")
    assert client.requests[0].schema == FactsDraft.model_json_schema()


def test_field_rules_beyond_the_schema_are_repaired() -> None:
    # Выводы есть, и причина их отсутствия тоже указана: форма верна, связь полей — нет.
    client = ScriptedClient([facts_answer(conclusions_absent_reason="нет"), facts_answer()])

    assert ask(client).attempts == 2
    assert "причина указывается" in client.requests[1].messages[-1].content


def test_stage_check_problems_are_repaired() -> None:
    client = ScriptedClient([facts_answer(), facts_answer()])
    calls: list[int] = []

    def check(_: FactsDraft) -> list[str]:
        calls.append(1)
        return ["метки S9 нет в материалах доклада"] if len(calls) == 1 else []

    assert ask(client, check=check).attempts == 2
    assert "метки S9 нет" in client.requests[1].messages[-1].content


def test_three_invalid_answers_fail_with_diagnostics() -> None:
    client = ScriptedClient(["{}", "[]", "не JSON"])

    with pytest.raises(StageError) as error:
        ask(client)
    assert error.value.code is FailureCode.LLM_INVALID_OUTPUT
    assert "попытка 1" in error.value.message and "попытка 3" in error.value.message
    assert len(client.requests) == 3


def test_answer_cut_by_token_limit_is_an_error_not_a_retry() -> None:
    cut = ChatResponse("", "долгое рассуждение", "length", 500, 2000, 1.0)
    client = ScriptedClient([cut, facts_answer()])

    with pytest.raises(StageError) as error:
        ask(client)
    assert error.value.code is FailureCode.LLM_TRUNCATED
    assert len(client.requests) == 1


def test_exhausted_context_is_an_error() -> None:
    full = ChatResponse(facts_answer(), "", "stop", 15000, 1000, 1.0)

    with pytest.raises(StageError) as error:
        ask(ScriptedClient([full]))
    assert error.value.code is FailureCode.CONTEXT_OVERFLOW


def test_materials_too_long_for_the_context_fail_before_the_call() -> None:
    client = ScriptedClient([facts_answer()])
    huge = [Message("system", "Факты."), Message("user", "слово " * 40000)]

    with pytest.raises(StageError) as error:
        ask_structured(client, PROFILE, PROMPT, huge, FactsDraft, think=True, max_repairs=2, seed=1)
    assert error.value.code is FailureCode.CONTEXT_OVERFLOW
    assert client.requests == []


def test_repository_prompts_render() -> None:
    facts = load_prompt("facts", "v1")
    summary = load_prompt("summary", "v1")

    assert "listener_remarks" in facts.render("system")
    assert "- метки S9 нет" in facts.render("repair", problems=["метки S9 нет"])
    system = summary.render("system", min_sentences=4, max_sentences=5)
    assert "от 4 до 5 предложений" in system


def test_prompt_with_missing_section_or_variable_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "facts").mkdir()
    (tmp_path / "facts" / "v9.md").write_text("## system\nТекст\n## user\n{{ blocks }}\n")
    with pytest.raises(PromptError, match="repair"):
        load_prompt("facts", "v9", tmp_path)

    (tmp_path / "facts" / "v9.md").write_text(
        "## system\nТекст\n## user\n{{ blocks }}\n## repair\nИсправь\n"
    )
    with pytest.raises(PromptError, match="blocks"):
        load_prompt("facts", "v9", tmp_path).render("user")


def test_profiles_file_has_a_valid_default() -> None:
    profile = load_profile(path=PROFILES_FILE)

    assert profile.name == "local" and profile.generation.reasoning
    assert load_profile("local-fast").generation.reasoning is False
    with pytest.raises(ProfileError, match="Нет профиля"):
        load_profile("нет-такого")
