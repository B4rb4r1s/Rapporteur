"""Шаг А spec §6.7. Факты и дословные цитаты из пакета фрагментов одного доклада.

Вход — только пакет этого доклада (изоляция докладов, spec §3, п. 1). Ответ модели проверяют
схема, валидаторы FactsDraft и проверка меток: ссылка на метку вне пакета уходит на
исправление. Найдена ли цитата, проверяет следующий шаг (verify.py).
"""

from collections.abc import Iterator
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

from rapporteur.contracts import (
    FactsArtifact,
    FactsDraft,
    ModelProfile,
    PackedFragment,
    TalkFragmentPack,
)
from rapporteur.contracts.claims import FactDraft
from rapporteur.contracts.enums import FragmentKind, PackBlock
from rapporteur.errors import FailureCode, StageError
from rapporteur.llm.client import LlmClient, Message
from rapporteur.llm.profiles import load_profile
from rapporteur.llm.prompts import Prompt, load_prompt
from rapporteur.llm.structured import ask_structured
from rapporteur.settings import PROMPTS_DIR

# Версия шага входит в ключ кэша.
VERSION = "1.0.0"
STEP = "facts"

_HEADINGS = {
    FragmentKind.SLIDE: "Слайды",
    FragmentKind.SLIDE_DESCRIPTION: "Описания слайдов",
    FragmentKind.TRANSCRIPT: "Стенограмма",
    FragmentKind.NOTE: "Заметки слушателей",
}


class FactsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # По умолчанию — профиль default из profiles.yaml (spec §9).
    profile: ModelProfile = Field(default_factory=load_profile)
    # Попытки исправить невалидный ответ после первой (backlog P1-06: не больше двух).
    max_repairs: NonNegativeInt = 2
    seed: int = 1


@dataclass(frozen=True)
class FactsBlock:
    heading: str
    fragments: list[PackedFragment]


def extract_facts(
    client: LlmClient, pack: TalkFragmentPack, config: FactsConfig | None = None
) -> FactsArtifact:
    config = config or FactsConfig()
    if not pack.fragments:
        raise StageError(FailureCode.NO_SOURCES, "В материалах доклада нет текста")
    profile = config.profile
    prompt = load_prompt(STEP, profile.prompt_version, PROMPTS_DIR)
    result = ask_structured(
        client,
        profile,
        prompt,
        facts_messages(prompt, pack),
        FactsDraft,
        think=profile.generation.reasoning,
        max_repairs=config.max_repairs,
        seed=config.seed,
        check=lambda facts: unknown_labels(facts, pack),
    )
    return FactsArtifact(
        talk_id=pack.talk_id,
        profile=profile.name,
        prompt_version=prompt.version,
        facts=result.value,
        attempts=result.attempts,
    )


def facts_messages(prompt: Prompt, pack: TalkFragmentPack) -> list[Message]:
    blocks = [
        FactsBlock(heading, fragments)
        for heading, fragments in _blocks(pack)
        if fragments  # пустые блоки не показываются модели
    ]
    return [
        Message("system", prompt.render("system")),
        Message("user", prompt.render("user", blocks=blocks)),
    ]


def _blocks(pack: TalkFragmentPack) -> Iterator[tuple[str, list[PackedFragment]]]:
    for kind, heading in _HEADINGS.items():
        main = [f for f in pack.fragments if f.kind is kind and f.block is PackBlock.MAIN]
        yield heading, main
    questions = [f for f in pack.fragments if f.block is PackBlock.QA]
    yield "Вопросы и ответы", questions


def unknown_labels(facts: FactsDraft, pack: TalkFragmentPack) -> list[str]:
    known = pack.by_label()
    problems: list[str] = []
    for fact in _all_facts(facts):
        problems.extend(
            f"метки {item.label} нет в материалах доклада (факт «{fact.text}»)"
            for item in fact.evidence
            if item.label not in known
        )
    return problems


def _all_facts(facts: FactsDraft) -> Iterator[FactDraft]:
    if facts.problem is not None:
        yield facts.problem
    yield from facts.methods
    yield from facts.results
    yield from facts.conclusions
    yield from facts.listener_remarks
