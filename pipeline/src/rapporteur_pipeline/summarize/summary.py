"""Этап summary: шаги В и Г spec §6.7 без критика (критик — Ф4).

Модель пишет текст только из утверждений, прошедших сверку. Код проверяет ссылки на
утверждения, число предложений, числа и запрещённые слова; при нарушениях сводка пишется
заново с замечаниями, до max_attempts раз. После этого она получает статус needs_attention:
в неё попадает лучшая попытка без строк с недопустимыми ссылками, а решение за человеком.
Статус доклада меняет оркестратор: доклад остаётся processed (ADR-009).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt

from rapporteur_contracts import (
    Claim,
    FactsArtifact,
    ModelProfile,
    StageInputs,
    StageOutput,
    Summary,
    SummaryArtifact,
    SummaryDraft,
    SummaryLine,
    TalkFragmentPack,
    TalkSummaryBundle,
    VerifiedClaimsArtifact,
)
from rapporteur_contracts.claims import CheckResult
from rapporteur_contracts.enums import CheckName, ClaimType, SummaryStatus
from rapporteur_pipeline.llm.client import LlmClient, Message
from rapporteur_pipeline.llm.profiles import load_profile
from rapporteur_pipeline.llm.prompts import PROMPTS_DIR, load_prompt
from rapporteur_pipeline.llm.structured import ask_structured
from rapporteur_pipeline.stage import StageContext, make_meta, publish, read_input
from rapporteur_pipeline.summarize.checks import (
    SummaryChecksConfig,
    check_summary,
    sentence_range,
    usable_claims,
)
from rapporteur_pipeline.summarize.pack import read_pack

FACTS_INPUT = "facts"
VERIFY_INPUT = "verify"
STEP = "summary"

_SUMMARY_NAMESPACE = uuid.UUID("9a4e2c71-3b5d-4f8e-a6c0-7d1b2e3f4a5c")
_KIND = {
    ClaimType.PROBLEM: "задача",
    ClaimType.METHOD: "метод",
    ClaimType.RESULT: "результат",
    ClaimType.CONCLUSION: "вывод",
}


class SummaryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Без профиля в конфигурации этапа — профиль по умолчанию из pipeline/profiles.yaml (ADR-013).
    profile: ModelProfile = Field(default_factory=load_profile)
    checks: SummaryChecksConfig = SummaryChecksConfig()
    # Сколько раз сводка пишется с замечаниями проверок, прежде чем уйти человеку.
    max_attempts: PositiveInt = 3
    # Исправления формы ответа внутри одной попытки.
    max_repairs: NonNegativeInt = 1
    seed: int = 1


@dataclass(frozen=True)
class ClaimLine:
    key: str
    kind: str
    text: str


@dataclass(frozen=True)
class Written:
    status: SummaryStatus
    sentences: list[SummaryLine]
    conclusions: list[SummaryLine]
    checks: list[CheckResult]
    attempts: int


class SummaryStage:
    name: ClassVar[str] = "summary"
    version: ClassVar[str] = "1.1.0"
    config_model: ClassVar[type[BaseModel]] = SummaryConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        config = ctx.config
        assert isinstance(config, SummaryConfig)
        if ctx.llm is None:
            raise ValueError("этапу summary нужен клиент модели")
        facts = FactsArtifact.model_validate_json(read_input(ctx, inputs, FACTS_INPUT))
        verified = VerifiedClaimsArtifact.model_validate_json(read_input(ctx, inputs, VERIFY_INPUT))
        pack = read_pack(ctx, inputs)
        if not facts.talk_id == verified.talk_id == pack.talk_id:
            raise ValueError("факты, утверждения и пакет относятся к разным докладам")

        written = write_summary(ctx.llm, config, facts.facts.title, verified.claims, pack)
        meta = make_meta(self, inputs, stub=False)
        summary = make_summary(
            meta.cache_key,
            pack,
            facts,
            verified.claims,
            written,
            profile=config.profile.name,
            prompt_version=config.profile.prompt_version,
        )
        warnings = []
        if written.status is SummaryStatus.NEEDS_ATTENTION:
            failed = sorted({c.check.value for c in written.checks if not c.passed})
            warnings.append(f"Сводка требует внимания человека: не пройдены {', '.join(failed)}")
        elif written.attempts > 1:
            warnings.append(f"Сводка принята с попытки {written.attempts}")
        return publish(ctx, meta, SummaryArtifact(meta=meta, summary=summary), warnings)


def write_summary(
    client: LlmClient,
    config: SummaryConfig,
    title: str | None,
    claims: Sequence[Claim],
    pack: TalkFragmentPack,
) -> Written:
    usable = usable_claims(claims)
    if not usable:
        missing = CheckResult(
            check=CheckName.CLAIM_LINKS,
            passed=False,
            attempt=1,
            details="нет утверждений, подтверждённых цитатами",
        )
        return Written(SummaryStatus.NEEDS_ATTENTION, [], [], [missing], 0)

    profile = config.profile
    prompt = load_prompt(STEP, profile.prompt_version, PROMPTS_DIR)
    low, high = sentence_range(config.checks, claims)
    lines = [ClaimLine(claim.key, _KIND[claim.type], claim.text) for claim in usable]
    messages = [
        Message("system", prompt.render("system", min_sentences=low, max_sentences=high)),
        Message(
            "user",
            prompt.render(
                "user",
                title=title,
                claims=lines,
                has_conclusions=any(claim.type is ClaimType.CONCLUSION for claim in usable),
            ),
        ),
    ]
    checks: list[CheckResult] = []
    best: tuple[int, SummaryDraft] | None = None
    for attempt in range(1, config.max_attempts + 1):
        result = ask_structured(
            client,
            profile,
            prompt,
            messages,
            SummaryDraft,
            think=profile.generation.reasoning,
            max_repairs=config.max_repairs,
            seed=config.seed,
        )
        draft = _punctuated(result.value)
        current = check_summary(draft, claims, pack, config.checks, attempt)
        checks.extend(current)
        failed = [check for check in current if not check.passed]
        if not failed:
            return Written(SummaryStatus.READY, draft.sentences, draft.conclusions, checks, attempt)
        if best is None or len(failed) <= best[0]:
            best = (len(failed), draft)
        problems = [check.details or check.check.value for check in failed]
        messages = [*result.messages, Message("user", prompt.render("repair", problems=problems))]

    assert best is not None
    draft = best[1]
    return Written(
        SummaryStatus.NEEDS_ATTENTION,
        _linked(draft.sentences, claims, conclusions=False),
        _linked(draft.conclusions, claims, conclusions=True),
        checks,
        config.max_attempts,
    )


_TERMINAL = ".!?…"
_CLOSING = '»")]'


def _terminated(text: str) -> str:
    """Строка сводки — законченное предложение. Модель иногда опускает точку, и в справке
    предложения слились бы в одно; точка ставится после закрывающих кавычек и скобок."""
    stripped = text.rstrip()
    core = stripped.rstrip(_CLOSING)
    if not core or core[-1] in _TERMINAL:
        return stripped
    return stripped + "."


def _punctuated(draft: SummaryDraft) -> SummaryDraft:
    def fix(lines: Sequence[SummaryLine]) -> list[SummaryLine]:
        return [line.model_copy(update={"text": _terminated(line.text)}) for line in lines]

    return draft.model_copy(
        update={"sentences": fix(draft.sentences), "conclusions": fix(draft.conclusions)}
    )


def _linked(
    lines: Sequence[SummaryLine], claims: Sequence[Claim], *, conclusions: bool
) -> list[SummaryLine]:
    """Строка остаётся, только если все её ссылки допустимы: текст, опирающийся на
    отклонённое утверждение, не сохраняется даже частично."""
    allowed = {
        claim.key: claim
        for claim in usable_claims(claims)
        if not conclusions or claim.type is ClaimType.CONCLUSION
    }
    return [line for line in lines if all(key in allowed for key in line.claim_ids)]


def make_summary(
    seed: str,
    pack: TalkFragmentPack,
    facts: FactsArtifact,
    claims: Sequence[Claim],
    written: Written,
    *,
    profile: str,
    prompt_version: str,
) -> Summary:
    """Версию сводки ведёт БД; конвейер выдаёт первую. Пакет и сводка проверяются вместе
    (TalkSummaryBundle) до публикации."""
    summary = Summary(
        id=uuid.uuid5(_SUMMARY_NAMESPACE, seed),
        talk_id=pack.talk_id,
        version=1,
        status=written.status,
        profile=profile,
        prompt_versions={FACTS_INPUT: facts.prompt_version, STEP: prompt_version},
        input_revision_ids=[fragment.revision_id for fragment in pack.fragments],
        facts=facts.facts,
        claims=list(claims),
        sentences=written.sentences,
        conclusions=written.conclusions,
        checks=written.checks,
        edited_by=None,
        created_at=datetime.now(UTC),
    )
    TalkSummaryBundle(pack=pack, summary=summary)
    return summary
