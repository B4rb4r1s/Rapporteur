"""Клиент-абстракция LLM (ADR-010). Шаги сводки видят запрос и ответ; движок — забота клиента."""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from rapporteur.contracts import ModelProfile
from rapporteur.contracts.enums import InferenceEngine
from rapporteur.errors import FailureCode, StageError

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[Message, ...]
    # JSON Schema ответа; грамматика проверяет только форму, связи полей — Pydantic.
    schema: dict[str, Any] | None
    think: bool
    temperature: float
    max_tokens: int
    context_tokens: int
    seed: int | None = None


@dataclass(frozen=True)
class ChatResponse:
    content: str
    thinking: str
    # stop — ответ закончен, length — оборван лимитом токенов.
    done_reason: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float


class LlmClient(Protocol):
    def chat(self, request: ChatRequest) -> ChatResponse: ...


class OllamaClient:
    """/api/chat Ollama: think, format, num_ctx, num_predict (ADR-010)."""

    def __init__(self, base_url: str, timeout_s: float = 1800) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def chat(self, request: ChatRequest) -> ChatResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": False,
            "think": request.think,
            "options": {
                "temperature": request.temperature,
                "num_ctx": request.context_tokens,
                "num_predict": request.max_tokens,
                **({"seed": request.seed} if request.seed is not None else {}),
            },
        }
        if request.schema is not None:
            body["format"] = request.schema
        http_request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            json.dumps(body, ensure_ascii=False).encode(),
            {"Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_s) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            raise StageError(
                FailureCode.LLM_UNAVAILABLE, f"Ollama ответила {error.code}: {detail}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise StageError(
                FailureCode.LLM_UNAVAILABLE, f"Ollama недоступна по {self.base_url}: {error}"
            ) from error
        message = data.get("message") or {}
        return ChatResponse(
            content=message.get("content") or "",
            thinking=message.get("thinking") or "",
            done_reason=data.get("done_reason") or "",
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            seconds=time.monotonic() - started,
        )


class ScriptedClient:
    """Отвечает заданными ответами по очереди и запоминает запросы. Для тестов."""

    def __init__(self, responses: Iterable[ChatResponse | str]) -> None:
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("у ScriptedClient закончились ответы")
        response = self.responses.pop(0)
        if isinstance(response, str):
            return ChatResponse(response, "", "stop", 100, len(response) // 3, 0.0)
        return response


def make_client(profile: ModelProfile, ollama_url: str) -> LlmClient:
    if profile.engine is InferenceEngine.OLLAMA:
        return OllamaClient(ollama_url)
    raise ValueError(f"движок {profile.engine.value} не поддерживается")
