"""Пакет доклада, этап facts (P1-06), проверки и этап summary (P1-08). Модель — ScriptedClient."""

import json
from pathlib import Path

import pytest

from rapporteur_contracts import (
    FactsArtifact,
    FactsDraft,
    Summary,
    SummaryArtifact,
    SummaryDraft,
    TalkFragmentPack,
)
from rapporteur_contracts.enums import CheckName, ClaimStatus, SummaryStatus
from rapporteur_pipeline.chain import Ran, run_cached
from rapporteur_pipeline.llm.client import ScriptedClient
from rapporteur_pipeline.stage import FailureCode, StageError
from rapporteur_pipeline.storage import DirectoryStorage
from rapporteur_pipeline.summarize.checks import SummaryChecksConfig, check_summary
from rapporteur_pipeline.summarize.facts import FactsConfig, FactsStage
from rapporteur_pipeline.summarize.pack import PackSource, build_pack
from rapporteur_pipeline.summarize.quotes import QuoteConfig
from rapporteur_pipeline.summarize.summary import SummaryConfig, SummaryStage
from rapporteur_pipeline.summarize.verify import VerifyStage, verify_facts

from .fictional_talk import (
    CONCLUSIONS,
    CONFERENCE,
    DECK,
    NOTES,
    PROFILE,
    SENTENCES,
    TALK,
    facts_answer,
    line,
    note_draft,
    pack,
    pack_blob,
    slide_draft,
    summary_answer,
)

CHECKS = SummaryChecksConfig()


@pytest.fixture
def storage(tmp_path: Path) -> DirectoryStorage:
    return DirectoryStorage(tmp_path / "data")


def run_facts(storage: DirectoryStorage, *answers: str) -> tuple[Ran, ScriptedClient]:
    client = ScriptedClient(answers)
    ref = pack_blob(storage, pack())
    ran = run_cached(FactsStage(), storage, FactsConfig(profile=PROFILE), [ref], llm=client)
    return ran, client


def run_summary(storage: DirectoryStorage, *answers: str) -> tuple[Summary, Ran, ScriptedClient]:
    facts, _ = run_facts(storage, facts_answer())
    ref = pack_blob(storage, pack())
    verify = run_cached(VerifyStage(), storage, QuoteConfig(), [facts.ref, ref])
    client = ScriptedClient(answers)
    ran = run_cached(
        SummaryStage(),
        storage,
        SummaryConfig(profile=PROFILE),
        [verify.ref, facts.ref, ref],
        llm=client,
    )
    summary = SummaryArtifact.model_validate_json(storage.get(ran.ref.storage_key)).summary
    return summary, ran, client


def test_pack_labels_slides_per_presentation_and_notes_in_a_row() -> None:
    two_decks = build_pack(
        CONFERENCE,
        TALK,
        [
            PackSource(DECK, [slide_draft(1, "Первая презентация"), slide_draft(2, " ")]),
            PackSource(NOTES, [slide_draft(3, "Вторая презентация")]),
        ],
        [
            PackSource(DECK, [note_draft(1, "Первая заметка")]),
            PackSource(NOTES, [note_draft(4, "Вторая заметка")]),
        ],
    )

    assert [f.label for f in two_decks.fragments] == ["S1.1", "S2.3", "N1", "N2"]
    assert [f.label for f in pack().fragments] == ["S1", "S2", "S3", "S4", "S5", "N1"]
    # Тот же вход — те же идентификаторы: ключ кэша не меняется между запусками.
    assert pack() == pack()


def test_facts_stage_publishes_facts_with_profile_and_prompt(storage: DirectoryStorage) -> None:
    ran, client = run_facts(storage, facts_answer())

    artifact = FactsArtifact.model_validate_json(storage.get(ran.ref.storage_key))
    assert (artifact.talk_id, artifact.profile, artifact.prompt_version) == (TALK, "test", "v1")
    assert artifact.facts.results[0].text == "LFU снизила задержку на 23 %"
    sent = client.requests[0]
    assert sent.think is True and sent.schema == FactsDraft.model_json_schema()
    user = sent.messages[1].content
    assert "[S4] Результаты: при нагрузке 1000 запросов" in user
    assert "Заметки слушателей:\n[N1]" in user
    assert ran.warnings == []


def test_unknown_label_is_sent_back_for_repair(storage: DirectoryStorage) -> None:
    wrong = json.loads(facts_answer())
    wrong["results"][0]["evidence"][0]["label"] = "S9"

    ran, client = run_facts(storage, json.dumps(wrong, ensure_ascii=False), facts_answer())

    assert "метки S9 нет в материалах доклада" in client.requests[1].messages[-1].content
    assert ran.warnings == ["Ответ модели принят с попытки 2"]


def test_facts_stage_is_cached(storage: DirectoryStorage) -> None:
    first, _ = run_facts(storage, facts_answer())
    second, client = run_facts(storage)

    assert second.cached and second.ref == first.ref and client.requests == []


def test_empty_pack_is_a_controlled_failure(storage: DirectoryStorage) -> None:
    empty = TalkFragmentPack(conference_id=CONFERENCE, talk_id=TALK, fragments=[])
    ref = pack_blob(storage, empty)

    with pytest.raises(StageError) as error:
        run_cached(
            FactsStage(), storage, FactsConfig(profile=PROFILE), [ref], llm=ScriptedClient([])
        )
    assert error.value.code is FailureCode.NO_SOURCES


def checks_of(
    sentences: list[dict[str, object]], conclusions: list[dict[str, object]]
) -> dict[CheckName, str | None]:
    claims = verify_facts(
        FactsDraft.model_validate_json(facts_answer()), pack(), QuoteConfig(), "t"
    )
    draft = SummaryDraft.model_validate({"sentences": sentences, "conclusions": conclusions})
    return {
        result.check: result.details
        for result in check_summary(draft, claims, pack(), CHECKS, attempt=1)
        if not result.passed
    }


def test_good_summary_passes_every_check() -> None:
    assert checks_of(SENTENCES, CONCLUSIONS) == {}


def test_sentence_without_valid_claims_is_not_accepted() -> None:
    remark = [*SENTENCES[:3], line("Слушатели спросили про холодный старт.", "c5")]
    unknown = [*SENTENCES[:3], line("Работа продолжается.", "c9")]
    wrong_conclusion = [line("LFU снизила задержку на 23 %.", "c3")]

    assert "c5 — реплика слушателя" in str(checks_of(remark, CONCLUSIONS)[CheckName.CLAIM_LINKS])
    assert "неизвестное утверждение c9" in str(
        checks_of(unknown, CONCLUSIONS)[CheckName.CLAIM_LINKS]
    )
    assert "c3 не вывод докладчика" in str(
        checks_of(SENTENCES, wrong_conclusion)[CheckName.CLAIM_LINKS]
    )
    assert "блок выводов пуст" in str(checks_of(SENTENCES, [])[CheckName.CLAIM_LINKS])


def test_link_to_rejected_claim_is_not_accepted() -> None:
    facts = json.loads(facts_answer())
    facts["methods"][0]["evidence"][0]["quote"] = "сравнение политик LRU и MRU на стенде из 8 узлов"
    claims = verify_facts(FactsDraft.model_validate(facts), pack(), QuoteConfig(), "t")
    draft = SummaryDraft.model_validate({"sentences": SENTENCES, "conclusions": CONCLUSIONS})

    assert claims[1].status is ClaimStatus.REJECTED
    failed = [r for r in check_summary(draft, claims, pack(), CHECKS, 1) if not r.passed]
    assert [r.check for r in failed] == [CheckName.CLAIM_LINKS]
    assert "утверждение c2 не подтверждено цитатой" in str(failed[0].details)


def test_number_missing_from_sources_is_rejected() -> None:
    changed = [
        *SENTENCES[:2],
        line("Показано, что политика LFU снизила задержку на 25 %.", "c3"),
        SENTENCES[3],
    ]
    # 1 000 в другой записи — то же число.
    spaced = [*SENTENCES[:3], line("Сравнение шло при нагрузке 1 000 запросов в секунду.", "c3")]

    assert checks_of(changed, CONCLUSIONS) == {
        CheckName.NUMBERS_IN_SOURCES: "числа 25 нет в материалах доклада"
    }
    assert checks_of(spaced, CONCLUSIONS) == {}


def test_sentence_count_and_forbidden_words() -> None:
    short = SENTENCES[:3]
    praised = [*SENTENCES[:3], line("Мы считаем этот уникальный подход перспективным.", "c3")]

    assert checks_of(short, CONCLUSIONS) == {
        CheckName.SENTENCE_COUNT: "в тексте 3 предложений, нужно 4–5"
    }
    assert checks_of(praised, CONCLUSIONS) == {
        CheckName.FORBIDDEN_PHRASES: "запрещённые слова: мы, уникальный"
    }


def test_summary_stage_writes_a_ready_summary(storage: DirectoryStorage) -> None:
    summary, ran, client = run_summary(storage, summary_answer())

    assert summary.status is SummaryStatus.READY
    assert summary.prompt_versions == {"facts": "v1", "summary": "v1"}
    assert summary.input_revision_ids == [f.revision_id for f in pack().fragments]
    assert [s.text for s in summary.sentences] == [s["text"] for s in SENTENCES]
    assert all(check.passed and check.attempt == 1 for check in summary.checks)
    user = client.requests[0].messages[1].content
    # Реплики слушателей и отклонённые утверждения модели не показываются.
    assert "c4 [вывод] LFU эффективнее LRU" in user and "c5" not in user
    assert ran.warnings == []


def test_lines_without_a_full_stop_get_one(storage: DirectoryStorage) -> None:
    """Модель иногда опускает точку; в справке такие предложения слились бы в одно."""
    bare = [line(s["text"].rstrip("."), *s["claim_ids"]) for s in SENTENCES]
    bare[1] = line("Автор сравнил политики «LRU» и «LFU»", "c2")
    conclusions = [line("LFU эффективнее LRU при неравномерной нагрузке", "c4")]

    summary, _, _ = run_summary(storage, summary_answer(bare, conclusions))

    assert summary.status is SummaryStatus.READY
    assert summary.sentences[0].text == SENTENCES[0]["text"]
    assert summary.sentences[1].text == "Автор сравнил политики «LRU» и «LFU»."
    assert summary.conclusions[0].text == "LFU эффективнее LRU при неравномерной нагрузке."


def test_failed_checks_regenerate_the_summary(storage: DirectoryStorage) -> None:
    wrong = [
        *SENTENCES[:2],
        line("Показано, что политика LFU снизила задержку на 25 %.", "c3"),
        SENTENCES[3],
    ]

    summary, ran, client = run_summary(storage, summary_answer(wrong), summary_answer())

    assert summary.status is SummaryStatus.READY
    assert "числа 25 нет в материалах доклада" in client.requests[1].messages[-1].content
    assert [(c.check, c.attempt) for c in summary.checks if not c.passed] == [
        (CheckName.NUMBERS_IN_SOURCES, 1)
    ]
    assert ran.warnings == ["Сводка принята с попытки 2"]


def test_summary_needs_attention_after_n_failures(storage: DirectoryStorage) -> None:
    bad = [*SENTENCES[:3], line("Слушатели спросили про холодный старт.", "c5")]
    answer = summary_answer(bad)

    summary, ran, client = run_summary(storage, answer, answer, answer)

    assert summary.status is SummaryStatus.NEEDS_ATTENTION
    assert len(client.requests) == 3
    # Строка со ссылкой на реплику слушателя в сводку не попадает.
    assert [s.text for s in summary.sentences] == [s["text"] for s in SENTENCES[:3]]
    assert ran.warnings == ["Сводка требует внимания человека: не пройдены claim_links"]
