"""Ответ модели по схеме с повторами (spec §6.7, шаг А; ADR-010, п. 6).

Грамматика Ollama проверяет только форму ответа. Связи между полями проверяют валидаторы
контракта и проверка этапа; невалидный ответ уходит на исправление не больше max_repairs раз,
затем — контролируемая ошибка с диагностикой. Ответ, оборванный лимитом токенов, — ошибка,
а не пустой результат: на рассуждение может уйти весь лимит.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from rapporteur_contracts import ModelProfile
from rapporteur_pipeline.llm.client import ChatRequest, ChatResponse, LlmClient, Message
from rapporteur_pipeline.llm.prompts import Prompt
from rapporteur_pipeline.stage import FailureCode, StageError

# Грубая оценка до вызова: кириллица в токенизаторах Qwen — около 3–4 символов на токен.
# Оценка оптимистичная и отсекает только заведомое переполнение; точную даёт ответ модели.
_CHARS_PER_TOKEN = 4
_MAX_PROBLEMS = 8


@dataclass(frozen=True)
class Structured[T: BaseModel]:
    value: T
    attempts: int
    responses: list[ChatResponse]
    # Диалог с последним принятым ответом: шаг Г продолжает его замечаниями проверок.
    messages: tuple[Message, ...]


def ask_structured[T: BaseModel](
    client: LlmClient,
    profile: ModelProfile,
    prompt: Prompt,
    messages: Sequence[Message],
    output: type[T],
    *,
    think: bool,
    max_repairs: int,
    seed: int | None,
    check: Callable[[T], list[str]] | None = None,
) -> Structured[T]:
    schema = output.model_json_schema()
    generation = profile.generation
    history = list(messages)
    _check_budget(history, schema, generation.max_tokens, generation.context_tokens)

    responses: list[ChatResponse] = []
    log: list[str] = []
    for attempt in range(1, max_repairs + 2):
        response = client.chat(
            ChatRequest(
                model=profile.model,
                messages=tuple(history),
                schema=schema,
                think=think,
                temperature=generation.temperature,
                max_tokens=generation.max_tokens,
                context_tokens=generation.context_tokens,
                seed=seed,
            )
        )
        responses.append(response)
        if response.done_reason == "length":
            raise StageError(
                FailureCode.LLM_TRUNCATED,
                f"Ответ модели оборван лимитом {generation.max_tokens} токенов "
                f"(попытка {attempt}, рассуждение {len(response.thinking)} символов)",
            )
        if response.prompt_tokens + response.completion_tokens >= generation.context_tokens:
            raise StageError(
                FailureCode.CONTEXT_OVERFLOW,
                f"Запрос и ответ заняли весь контекст {generation.context_tokens} токенов",
            )
        value, problems = _parse(response.content, output, check)
        history.append(Message("assistant", response.content))
        if value is not None:
            return Structured(value, attempt, responses, tuple(history))
        log.append(f"попытка {attempt}: " + "; ".join(problems))
        history.append(Message("user", prompt.render("repair", problems=problems)))
    raise StageError(
        FailureCode.LLM_INVALID_OUTPUT,
        f"Ответ модели не прошёл проверку за {max_repairs + 1} попытки: " + " | ".join(log),
    )


def _check_budget(
    messages: Sequence[Message], schema: dict[str, Any], max_tokens: int, context_tokens: int
) -> None:
    chars = sum(len(message.content) for message in messages) + len(json.dumps(schema))
    estimate = chars // _CHARS_PER_TOKEN
    if estimate + max_tokens > context_tokens:
        raise StageError(
            FailureCode.CONTEXT_OVERFLOW,
            f"Материалы доклада — около {estimate} токенов, с резервом на ответ {max_tokens} "
            f"не помещаются в контекст {context_tokens}; деление на части ещё не реализовано",
        )


def _parse[T: BaseModel](
    content: str, output: type[T], check: Callable[[T], list[str]] | None
) -> tuple[T | None, list[str]]:
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as error:
        return None, [f"ответ не является JSON: {error.msg}"]
    try:
        value = output.model_validate(raw)
    except ValidationError as error:
        return None, [_describe(item) for item in error.errors()[:_MAX_PROBLEMS]]
    problems = check(value) if check is not None else []
    if problems:
        return None, problems[:_MAX_PROBLEMS]
    return value, []


def _describe(error: Any) -> str:
    place = ".".join(str(part) for part in error["loc"])
    return f"{place}: {error['msg']}" if place else str(error["msg"])
