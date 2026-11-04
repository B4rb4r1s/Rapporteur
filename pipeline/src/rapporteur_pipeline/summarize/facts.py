"""Этап facts: шаг А spec §6.7. Факты и дословные цитаты из пакета фрагментов одного доклада.

Вход — только пакет этого доклада (изоляция докладов, spec §3, п. 1). Ответ модели проверяют
схема, валидаторы FactsDraft и проверка меток: ссылка на метку вне пакета уходит на
исправление. Найдена ли цитата, проверяет следующий этап verify.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, NonNegativeInt

from rapporteur_contracts import (
    FactsArtifact,
    FactsDraft,
    ModelProfile,
    PackedFragment,
    StageInputs,
    StageOutput,
    TalkFragmentPack,
)
from rapporteur_contracts.claims import FactDraft
from rapporteur_contracts.enums import FragmentKind, PackBlock
from rapporteur_pipeline.llm.client import Message
from rapporteur_pipeline.llm.prompts import PROMPTS_DIR, Prompt, load_prompt
from rapporteur_pipeline.llm.structured import ask_structured
from rapporteur_pipeline.stage import (
    FailureCode,
    StageContext,
    StageError,
    make_meta,
    publish,
    read_input,
)

PACK_INPUT = "pack"
STEP = "facts"

_HEADINGS = {
    FragmentKind.SLIDE: "Слайды",
    FragmentKind.SLIDE_DESCRIPTION: "Описания слайдов",
    FragmentKind.TRANSCRIPT: "Стенограмма",
    FragmentKind.NOTE: "Заметки слушателей",
}


class FactsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: ModelProfile
    # Попытки исправить невалидный ответ после первой (backlog P1-06: не больше двух).
    max_repairs: NonNegativeInt = 2
    seed: int = 1


@dataclass(frozen=True)
class FactsBlock:
    heading: str
    fragments: list[PackedFragment]


class FactsStage:
    name: ClassVar[str] = "facts"
    version: ClassVar[str] = "1.0.0"
    config_model: ClassVar[type[BaseModel]] = FactsConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        config = ctx.config
        assert isinstance(config, FactsConfig)
        if ctx.llm is None:
            raise ValueError("этапу facts нужен клиент модели")
        pack = TalkFragmentPack.model_validate_json(read_input(ctx, inputs, PACK_INPUT))
        if inputs.talk_id is not None and inputs.talk_id != pack.talk_id:
            raise ValueError("пакет фрагментов относится к другому докладу")
        if not pack.fragments:
            raise StageError(FailureCode.NO_SOURCES, "В материалах доклада нет текста")

        profile = config.profile
        prompt = load_prompt(STEP, profile.prompt_version, PROMPTS_DIR)
        result = ask_structured(
            ctx.llm,
            profile,
            prompt,
            facts_messages(prompt, pack),
            FactsDraft,
            think=profile.generation.reasoning,
            max_repairs=config.max_repairs,
            seed=config.seed,
            check=lambda facts: unknown_labels(facts, pack),
        )
        meta = make_meta(self, inputs, stub=False)
        artifact = FactsArtifact(
            meta=meta,
            talk_id=pack.talk_id,
            profile=profile.name,
            prompt_version=prompt.version,
            facts=result.value,
        )
        warnings = []
        if result.attempts > 1:
            warnings.append(f"Ответ модели принят с попытки {result.attempts}")
        return publish(ctx, meta, artifact, warnings)


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
