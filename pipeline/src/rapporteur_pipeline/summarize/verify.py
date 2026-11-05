"""Этап verify: шаг Б spec §6.7. Факты модели становятся утверждениями со статусом.

Утверждение подтверждено, если хотя бы одна его цитата найдена в своём фрагменте. Без
подтверждённых цитат утверждение отклоняется, и сводка не может на него сослаться: это
проверяет модель Summary из contracts/. Опора только на описание слайда от VLM требует
проверки человеком (ADR-008, п. 8).
"""

import uuid
from collections.abc import Iterator
from typing import ClassVar

from pydantic import BaseModel

from rapporteur_contracts import (
    Claim,
    Evidence,
    FactsArtifact,
    FactsDraft,
    RejectedEvidence,
    StageInputs,
    StageOutput,
    TalkFragmentPack,
    VerifiedClaimsArtifact,
)
from rapporteur_contracts.claims import FactDraft
from rapporteur_contracts.enums import ClaimStatus, ClaimType, EvidenceRejection
from rapporteur_pipeline.stage import StageContext, make_meta, publish, read_input
from rapporteur_pipeline.summarize.pack import read_pack
from rapporteur_pipeline.summarize.quotes import QuoteConfig, match_quote

# Имена входов этапа: артефакт шага А и пакет фрагментов доклада, который собрал воркер.
FACTS_INPUT = "facts"

# Пространство имён для идентификаторов утверждений: один и тот же вход даёт те же id.
_CLAIM_NAMESPACE = uuid.UUID("6f1b5a8e-6c2e-4f4b-9a51-3d0c4b8f2a17")


class VerifyStage:
    name: ClassVar[str] = "verify"
    version: ClassVar[str] = "1.0.0"
    config_model: ClassVar[type[BaseModel]] = QuoteConfig

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        config = ctx.config
        assert isinstance(config, QuoteConfig)
        facts = FactsArtifact.model_validate_json(read_input(ctx, inputs, FACTS_INPUT))
        pack = read_pack(ctx, inputs)
        if facts.talk_id != pack.talk_id:
            raise ValueError("факты и пакет фрагментов относятся к разным докладам")

        meta = make_meta(self, inputs, stub=False)
        claims = verify_facts(facts.facts, pack, config, id_seed=meta.cache_key)
        rejected = [claim.key for claim in claims if claim.status is ClaimStatus.REJECTED]
        warnings = [f"Не подтверждены цитатами: {', '.join(rejected)}"] if rejected else []
        artifact = VerifiedClaimsArtifact(meta=meta, talk_id=pack.talk_id, claims=claims)
        return publish(ctx, meta, artifact, warnings)


def verify_facts(
    facts: FactsDraft, pack: TalkFragmentPack, config: QuoteConfig, id_seed: str
) -> list[Claim]:
    """Ключи c1, c2… идут в порядке: проблема, методы, результаты, выводы, реплики слушателей."""
    by_label = pack.by_label()
    claims: list[Claim] = []
    for claim_type, fact in _facts_in_order(facts):
        key = f"c{len(claims) + 1}"
        evidence: list[Evidence] = []
        rejected: list[RejectedEvidence] = []
        for item in fact.evidence:
            fragment = by_label.get(item.label)
            if fragment is None:
                rejected.append(
                    RejectedEvidence(
                        label=item.label,
                        quote=item.quote,
                        reason=EvidenceRejection.LABEL_NOT_FOUND,
                        best_score=None,
                    )
                )
                continue
            match = match_quote(item.quote, fragment.text, config)
            if match.rejection is not None or match.span is None or match.score is None:
                rejected.append(
                    RejectedEvidence(
                        label=item.label,
                        quote=item.quote,
                        reason=match.rejection or EvidenceRejection.QUOTE_NOT_FOUND,
                        best_score=match.score,
                    )
                )
                continue
            evidence.append(
                Evidence(
                    label=item.label,
                    quote=item.quote,
                    fragment_revision_id=fragment.revision_id,
                    span=match.span,
                    match_score=match.score,
                )
            )
        claims.append(
            Claim(
                id=uuid.uuid5(_CLAIM_NAMESPACE, f"{id_seed}:{key}"),
                key=key,
                type=claim_type,
                text=fact.text,
                status=_status(evidence),
                evidence=evidence,
                rejected_evidence=rejected,
            )
        )
    return claims


def _facts_in_order(facts: FactsDraft) -> Iterator[tuple[ClaimType, FactDraft]]:
    if facts.problem is not None:
        yield ClaimType.PROBLEM, facts.problem
    for claim_type, items in (
        (ClaimType.METHOD, facts.methods),
        (ClaimType.RESULT, facts.results),
        (ClaimType.CONCLUSION, facts.conclusions),
        (ClaimType.LISTENER_REMARK, facts.listener_remarks),
    ):
        for fact in items:
            yield claim_type, fact


def _status(evidence: list[Evidence]) -> ClaimStatus:
    if not evidence:
        return ClaimStatus.REJECTED
    if all(item.label.startswith("V") for item in evidence):
        return ClaimStatus.NEEDS_HUMAN
    return ClaimStatus.VERIFIED
