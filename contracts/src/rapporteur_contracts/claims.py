"""Факты, утверждения и сводка (spec §6.7, §7.3, ADR-003, ADR-008).

Модели с суффиксом Draft — ответы LLM. Их JSON Schema передаётся модели как ограничение вывода.
"""

from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, PositiveInt, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from rapporteur_contracts.base import (
    ContractModel,
    NonEmptyStr,
    Probability,
    Span,
    UtcDatetime,
)
from rapporteur_contracts.enums import (
    CheckName,
    ClaimStatus,
    ClaimType,
    EvidenceRejection,
    SummaryStatus,
    TalkType,
)
from rapporteur_contracts.fragments import Label, TalkFragmentPack

# Короткий ключ утверждения внутри сводки. UUID в промпте дороги и легко искажаются моделью.
ClaimKey = Annotated[str, StringConstraints(pattern=r"^c[1-9][0-9]*$")]


class EvidenceDraft(ContractModel):
    """Ссылка от модели: метка и дословная цитата."""

    label: Label
    quote: NonEmptyStr


class FactDraft(ContractModel):
    text: NonEmptyStr
    evidence: list[EvidenceDraft]


class FactsDraft(ContractModel):
    """Ответ шага А. title, speakers и affiliations — подсказка для сверки с реестром."""

    title: NonEmptyStr | None
    speakers: list[NonEmptyStr]
    affiliations: list[NonEmptyStr]
    talk_type: TalkType
    problem: FactDraft | None
    methods: list[FactDraft]
    results: list[FactDraft]
    conclusions: list[FactDraft]
    conclusions_absent_reason: NonEmptyStr | None
    listener_remarks: list[FactDraft]
    uncertain: list[NonEmptyStr]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if bool(self.conclusions) == bool(self.conclusions_absent_reason):
            raise PydanticCustomError(
                "conclusions_reason_mismatch",
                "причина указывается тогда и только тогда, когда выводов нет",
            )
        speaker_facts = [
            *([self.problem] if self.problem else []),
            *self.methods,
            *self.results,
            *self.conclusions,
        ]
        for fact in speaker_facts:
            if any(item.label.startswith("N") for item in fact.evidence):
                raise PydanticCustomError(
                    "note_outside_listener_remarks",
                    "заметки слушателей идут только в listener_remarks",
                )
        for fact in self.listener_remarks:
            if not all(item.label.startswith("N") for item in fact.evidence):
                raise PydanticCustomError(
                    "listener_remark_without_note", "реплика слушателя опирается только на заметки"
                )
        return self


class Evidence(ContractModel):
    """Подтверждённая ссылка. label и quote даёт модель, остальное вычисляет код (ADR-003)."""

    label: Label
    quote: NonEmptyStr
    fragment_revision_id: UUID
    span: Span
    match_score: Probability


class RejectedEvidence(ContractModel):
    """Цитата, не прошедшая сверку, с причиной отказа."""

    label: Label
    quote: NonEmptyStr
    reason: EvidenceRejection
    best_score: Probability | None


class Claim(ContractModel):
    """Утверждение. key адресует его внутри сводки, id — в БД и API."""

    id: UUID
    key: ClaimKey
    type: ClaimType
    text: NonEmptyStr
    status: ClaimStatus
    evidence: list[Evidence]
    rejected_evidence: list[RejectedEvidence]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.status is ClaimStatus.REJECTED) == bool(self.evidence):
            raise PydanticCustomError(
                "claim_evidence_mismatch",
                "у отклонённого утверждения нет подтверждённых цитат, у остальных они обязательны",
            )
        labels = [item.label for item in self.evidence]
        from_notes = [label.startswith("N") for label in labels]
        if self.type is ClaimType.LISTENER_REMARK and not all(from_notes):
            raise PydanticCustomError(
                "listener_remark_without_note", "реплика слушателя опирается только на заметки"
            )
        if self.type is not ClaimType.LISTENER_REMARK and any(from_notes):
            raise PydanticCustomError(
                "note_outside_listener_remarks",
                "заметки слушателей идут только в listener_remarks",
            )
        if self.status is ClaimStatus.VERIFIED and all(label.startswith("V") for label in labels):
            raise PydanticCustomError(
                "vlm_only_not_verified", "опора только на текст VLM требует проверки человеком"
            )
        return self


class SummaryLine(ContractModel):
    """Предложение сводки или пункт выводов со ссылками на утверждения."""

    text: NonEmptyStr
    claim_ids: list[ClaimKey] = Field(min_length=1)


class SummaryDraft(ContractModel):
    """Ответ шага В. Диапазон числа предложений настраивается и проверяется на шаге Г."""

    sentences: list[SummaryLine] = Field(min_length=1)
    conclusions: list[SummaryLine] = Field(max_length=3)


class CheckResult(ContractModel):
    check: CheckName
    passed: bool
    attempt: PositiveInt
    details: NonEmptyStr | None


class Summary(ContractModel):
    """Сводка доклада: версия, утверждения, текст и результаты всех проверок."""

    id: UUID
    talk_id: UUID
    version: PositiveInt
    status: SummaryStatus
    profile: NonEmptyStr
    prompt_versions: dict[NonEmptyStr, NonEmptyStr]
    input_revision_ids: list[UUID]
    facts: FactsDraft
    claims: list[Claim]
    sentences: list[SummaryLine]
    conclusions: list[SummaryLine]
    checks: list[CheckResult]
    edited_by: UUID | None
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        claims = {claim.key: claim for claim in self.claims}
        if len(claims) != len(self.claims):
            raise PydanticCustomError("duplicate_claim_key", "ключи утверждений уникальны")

        for line in self.sentences:
            self._check_line(line, claims)
        for line in self.conclusions:
            self._check_line(line, claims)
            if any(claims[key].type is not ClaimType.CONCLUSION for key in line.claim_ids):
                raise PydanticCustomError(
                    "conclusion_without_conclusion_claim",
                    "пункт выводов опирается только на выводы докладчика",
                )

        known_revisions = set(self.input_revision_ids)
        for claim in self.claims:
            for item in claim.evidence:
                if item.fragment_revision_id not in known_revisions:
                    raise PydanticCustomError(
                        "evidence_revision_not_in_input",
                        "ссылка указывает на ревизию вне входа сводки: {label}",
                        {"label": item.label},
                    )
        return self

    @staticmethod
    def _check_line(line: SummaryLine, claims: dict[str, Claim]) -> None:
        for key in line.claim_ids:
            claim = claims.get(key)
            if claim is None:
                raise PydanticCustomError(
                    "unknown_claim", "неизвестное утверждение {key}", {"key": key}
                )
            if claim.status is ClaimStatus.REJECTED:
                raise PydanticCustomError(
                    "rejected_claim", "утверждение {key} не прошло сверку", {"key": key}
                )
            if claim.type is ClaimType.LISTENER_REMARK:
                raise PydanticCustomError(
                    "listener_remark_in_text", "реплики слушателей не входят в текст сводки"
                )


class TalkSummaryBundle(ContractModel):
    """Сводка вместе с пакетом, по которому она собрана. Здесь проверяются правила,
    которые JSON Schema не выражает. Воркер строит этот объект до публикации результата."""

    pack: TalkFragmentPack
    summary: Summary

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.pack.talk_id != self.summary.talk_id:
            raise PydanticCustomError("talk_mismatch", "пакет и сводка относятся к разным докладам")

        by_label = self.pack.by_label()
        for claim in self.summary.claims:
            for item in claim.evidence:
                fragment = by_label.get(item.label)
                if fragment is None:
                    raise PydanticCustomError(
                        "unknown_label",
                        "метки {label} нет в пакете доклада",
                        {"label": item.label},
                    )
                if fragment.revision_id != item.fragment_revision_id:
                    raise PydanticCustomError(
                        "foreign_fragment",
                        "ссылка {label} указывает на ревизию вне пакета доклада",
                        {"label": item.label},
                    )
                if item.span[1] > len(fragment.text):
                    raise PydanticCustomError(
                        "span_out_of_range",
                        "диапазон {label} выходит за пределы текста",
                        {"label": item.label},
                    )
        return self
