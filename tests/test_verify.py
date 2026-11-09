"""Сверка цитат кодом (P1-07). Тексты вымышленные."""

import uuid

import pytest
from pydantic import ValidationError

from rapporteur.contracts import Claim, FactsDraft, TalkFragmentPack
from rapporteur.contracts.claims import EvidenceDraft, FactDraft
from rapporteur.contracts.enums import (
    ClaimStatus,
    ClaimType,
    EvidenceRejection,
    FragmentKind,
    PackBlock,
    TalkType,
    TextOrigin,
)
from rapporteur.contracts.fragments import LinesLocator, PackedFragment, PageLocator
from rapporteur.summarize.quotes import QuoteConfig, match_quote, search_form
from rapporteur.summarize.verify import verify_facts

TALK = uuid.UUID("00000000-0000-4000-8000-000000000010")
DECK = uuid.UUID("00000000-0000-4000-8000-000000000100")
NOTES = uuid.UUID("00000000-0000-4000-8000-000000000101")

RESULTS_SLIDE = (
    "Результаты: точность выросла до 94,5 %. Время обучения не увеличилось при росте выборки вдвое."
)
CACHE_SLIDE = "Ёмкость «кэша» — 8 ГБ, задержка 1 000 мкс."
NOTE = "Из зала спросили про холодный старт."
VLM = "На схеме три узла кластера и балансировщик."


def _fragment(label: str, text: str, kind: FragmentKind, number: int) -> PackedFragment:
    locator: PageLocator | LinesLocator
    if kind is FragmentKind.NOTE:
        locator = LinesLocator(kind="lines", line_start=number, line_end=number)
    else:
        locator = PageLocator(kind="page", page_no=number)
    return PackedFragment(
        label=label,
        kind=kind,
        fragment_id=uuid.uuid5(TALK, f"fragment:{label}"),
        revision_id=uuid.uuid5(TALK, f"revision:{label}"),
        source_file_id=NOTES if kind is FragmentKind.NOTE else DECK,
        locator=locator,
        origin=TextOrigin.VLM if kind is FragmentKind.SLIDE_DESCRIPTION else TextOrigin.EXTRACTED,
        text=text,
        block=PackBlock.MAIN,
        low_quality=False,
    )


PACK = TalkFragmentPack(
    conference_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
    talk_id=TALK,
    fragments=[
        _fragment("S1", RESULTS_SLIDE, FragmentKind.SLIDE, 1),
        _fragment("S2", CACHE_SLIDE, FragmentKind.SLIDE, 2),
        _fragment("V3", VLM, FragmentKind.SLIDE_DESCRIPTION, 3),
        _fragment("N1", NOTE, FragmentKind.NOTE, 1),
    ],
)
CONFIG = QuoteConfig()


def _fact(text: str, *evidence: tuple[str, str]) -> FactDraft:
    return FactDraft(
        text=text,
        evidence=[EvidenceDraft(label=label, quote=quote) for label, quote in evidence],
    )


def _verify(*results: FactDraft) -> list[Claim]:
    facts = FactsDraft(
        title=None,
        speakers=[],
        affiliations=[],
        talk_type=TalkType.RESEARCH,
        problem=None,
        methods=[],
        results=list(results),
        conclusions=[],
        conclusions_absent_reason="обзорный доклад",
        listener_remarks=[],
        uncertain=[],
    )
    return verify_facts(facts, PACK, CONFIG, id_seed="seed")


def _one(label: str, quote: str) -> Claim:
    return _verify(_fact("Утверждение", (label, quote)))[0]


def _quoted(claim: Claim, source: str) -> str:
    start, end = claim.evidence[0].span
    return source[start:end]


@pytest.mark.parametrize(
    "quote",
    [
        "точность выросла до 94,5 %",
        "Точность выросла до 94,5%",
        "точнось выросла до 94,5 %",
        "точность выросла до 94.5 %",
    ],
    ids=["exact", "case-and-spacing", "typo", "decimal-point"],
)
def test_exact_and_slightly_distorted_quotes_are_found(quote: str) -> None:
    claim = _one("S1", quote)

    assert claim.status is ClaimStatus.VERIFIED
    assert _quoted(claim, RESULTS_SLIDE) == "точность выросла до 94,5"
    assert claim.evidence[0].fragment_revision_id == PACK.by_label()["S1"].revision_id


def test_span_points_to_original_spelling_with_yo_quotes_and_dash() -> None:
    claim = _one("S2", "емкость кэша 8 ГБ")

    assert _quoted(claim, CACHE_SLIDE) == "Ёмкость «кэша» — 8 ГБ"


def test_thousands_separator_does_not_break_numbers() -> None:
    claim = _one("S2", "задержка 1000 мкс")

    assert claim.status is ClaimStatus.VERIFIED
    assert _quoted(claim, CACHE_SLIDE) == "задержка 1 000 мкс"


@pytest.mark.parametrize(
    ("label", "quote", "reason"),
    [
        ("S1", "точность выросла до 84,5 %", EvidenceRejection.NUMBER_MISMATCH),
        ("S1", "точность выросла до 95,4 %", EvidenceRejection.NUMBER_MISMATCH),
        (
            "S1",
            "Время обучения увеличилось при росте выборки вдвое",
            EvidenceRejection.NEGATION_MISMATCH,
        ),
        ("S1", "увеличилось при росте выборки вдвое", EvidenceRejection.NEGATION_MISMATCH),
        ("S1", "94,5 %", EvidenceRejection.TOO_SHORT),
        ("S1", "модель обучалась на трёх серверах", EvidenceRejection.QUOTE_NOT_FOUND),
        ("S9", "точность выросла до 94,5 %", EvidenceRejection.LABEL_NOT_FOUND),
        ("S2", "точность выросла до 94,5 %", EvidenceRejection.QUOTE_NOT_FOUND),
        (
            "S1",
            RESULTS_SLIDE + " Стоимость обучения снизилась вдвое.",
            EvidenceRejection.BELOW_THRESHOLD,
        ),
    ],
    ids=[
        "number-swapped",
        "digits-swapped",
        "negation-dropped",
        "negation-just-before-place",
        "too-short",
        "invented",
        "other-talk-label",
        "wrong-fragment",
        "invented-tail",
    ],
)
def test_bad_quotes_are_rejected_with_reason(
    label: str, quote: str, reason: EvidenceRejection
) -> None:
    claim = _one(label, quote)

    assert claim.status is ClaimStatus.REJECTED
    assert claim.evidence == []
    assert [item.reason for item in claim.rejected_evidence] == [reason]


def test_swapped_number_scores_above_threshold_yet_fails() -> None:
    """Ради этого случая числа сверяются точно (ADR-008)."""
    match = match_quote("точность выросла до 84,5 %", RESULTS_SLIDE, CONFIG)

    assert match.score is not None and match.score >= CONFIG.min_score
    assert match.rejection is EvidenceRejection.NUMBER_MISMATCH


def test_dropped_negation_scores_above_threshold_yet_fails() -> None:
    match = match_quote("Время обучения увеличилось при росте выборки вдвое", RESULTS_SLIDE, CONFIG)

    assert match.score is not None and match.score >= CONFIG.min_score
    assert match.rejection is EvidenceRejection.NEGATION_MISMATCH


def test_short_dropped_negation_is_rejected_anyway() -> None:
    """Пример из ADR-008: оценка 0,88 ниже порога, отказ приходит раньше проверки отрицаний."""
    claim = _one("S1", "время обучения увеличилось")

    assert claim.status is ClaimStatus.REJECTED


def test_claim_with_one_good_quote_is_verified_and_keeps_rejected_ones() -> None:
    claim = _verify(
        _fact(
            "Точность выросла",
            ("S1", "точность выросла до 84,5 %"),
            ("S1", "точность выросла до 94,5 %"),
        )
    )[0]

    assert claim.status is ClaimStatus.VERIFIED
    assert len(claim.evidence) == 1
    assert [item.reason for item in claim.rejected_evidence] == [EvidenceRejection.NUMBER_MISMATCH]


def test_types_keys_and_vlm_only_claims() -> None:
    facts = FactsDraft(
        title=None,
        speakers=[],
        affiliations=[],
        talk_type=TalkType.RESEARCH,
        problem=_fact("Ёмкость кэша мала", ("S2", "Ёмкость «кэша» — 8 ГБ")),
        methods=[_fact("Кластер из трёх узлов", ("V3", "три узла кластера и балансировщик"))],
        results=[_fact("Точность 94,5 %", ("S1", "точность выросла до 94,5 %"))],
        conclusions=[_fact("Обучение не замедлилось", ("S1", "Время обучения не увеличилось"))],
        conclusions_absent_reason=None,
        listener_remarks=[_fact("Спросили про холодный старт", ("N1", NOTE))],
        uncertain=[],
    )
    claims = verify_facts(facts, PACK, CONFIG, id_seed="seed")

    assert [(c.key, c.type) for c in claims] == [
        ("c1", ClaimType.PROBLEM),
        ("c2", ClaimType.METHOD),
        ("c3", ClaimType.RESULT),
        ("c4", ClaimType.CONCLUSION),
        ("c5", ClaimType.LISTENER_REMARK),
    ]
    assert [c.status for c in claims] == [
        ClaimStatus.VERIFIED,
        ClaimStatus.NEEDS_HUMAN,
        ClaimStatus.VERIFIED,
        ClaimStatus.VERIFIED,
        ClaimStatus.VERIFIED,
    ]
    assert claims == verify_facts(facts, PACK, CONFIG, id_seed="seed")


def test_search_form_maps_back_to_source() -> None:
    form = search_form("Ёмкость «кэша» — 8 ГБ")

    assert form.text == "емкость кэша 8 гб"
    assert form.source_span(0, len(form.text)) == (0, len("Ёмкость «кэша» — 8 ГБ"))


def test_config_rejects_inverted_thresholds() -> None:
    with pytest.raises(ValidationError):
        QuoteConfig(min_score=0.5, not_found_below=0.8)


def test_claim_ids_are_stable_for_the_same_input() -> None:
    facts = FactsDraft(
        title=None,
        speakers=[],
        affiliations=[],
        talk_type=TalkType.RESEARCH,
        problem=None,
        methods=[],
        results=[
            _fact("Точность 94,5 %", ("S1", "точность выросла до 94,5 %")),
            _fact("Точность 84,5 %", ("S1", "точность выросла до 84,5 %")),
        ],
        conclusions=[],
        conclusions_absent_reason="обзорный доклад",
        listener_remarks=[],
        uncertain=[],
    )

    claims = verify_facts(facts, PACK, CONFIG, id_seed="seed")

    assert [c.status for c in claims] == [ClaimStatus.VERIFIED, ClaimStatus.REJECTED]
    assert [c.id for c in claims] == [c.id for c in verify_facts(facts, PACK, CONFIG, "seed")]
    assert claims[0].id != verify_facts(facts, PACK, CONFIG, "other")[0].id
