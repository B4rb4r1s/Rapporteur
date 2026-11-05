"""Вымышленный доклад для шагов сводки: пакет, ответы модели, профиль (P1-06, P1-08)."""

import datetime as dt
import json
import uuid
from typing import Any

from rapporteur_contracts import (
    CONTRACT_VERSION,
    ArtifactMeta,
    ArtifactRef,
    FragmentDraft,
    ModelProfile,
    PackArtifact,
    TalkFragmentPack,
)
from rapporteur_contracts.entities import GenerationParams
from rapporteur_contracts.enums import FragmentKind, InferenceEngine, TextOrigin
from rapporteur_contracts.fragments import LinesLocator, PageLocator
from rapporteur_pipeline.chain import put_blob
from rapporteur_pipeline.storage import Storage
from rapporteur_pipeline.summarize.pack import PackSource, build_pack

CONFERENCE = uuid.UUID("00000000-0000-4000-8000-000000000001")
TALK = uuid.UUID("00000000-0000-4000-8000-000000000010")
DECK = uuid.UUID("00000000-0000-4000-8000-000000000100")
NOTES = uuid.UUID("00000000-0000-4000-8000-000000000101")

SLIDES = [
    "Кэширование запросов в распределённых базах данных. Иванов И. И., Институт примера.",
    "Задача: снизить задержку чтения при неравномерной нагрузке.",
    "Метод: сравнение политик LRU и LFU на стенде из 4 узлов с 32 ГБ памяти.",
    "Результаты: при нагрузке 1000 запросов в секунду политика LFU снизила задержку на 23 %.",
    "Выводы: LFU эффективнее LRU при неравномерном распределении запросов.",
]
NOTE = "Из зала спросили про холодный старт. Докладчик ответил, что он не исследовался."

PROFILE = ModelProfile(
    name="test",
    engine=InferenceEngine.OLLAMA,
    model="test-model",
    weights_revision="sha256:0",
    quantization="q4_K_M",
    generation=GenerationParams(
        temperature=0.1, reasoning=True, max_tokens=2000, context_tokens=16000
    ),
    prompt_version="v1",
    stages=["facts", "summary"],
)


def slide_draft(page: int, text: str) -> FragmentDraft:
    return FragmentDraft(
        kind=FragmentKind.SLIDE,
        source_sha256="a" * 64,
        locator=PageLocator(kind="page", page_no=page),
        raw_text=text,
        normalized_text=text,
        origin=TextOrigin.EXTRACTED,
        engine_confidence=None,
    )


def note_draft(line: int, text: str) -> FragmentDraft:
    return FragmentDraft(
        kind=FragmentKind.NOTE,
        source_sha256="b" * 64,
        locator=LinesLocator(kind="lines", line_start=line, line_end=line),
        raw_text=text,
        normalized_text=text,
        origin=TextOrigin.EXTRACTED,
        engine_confidence=None,
    )


def pack() -> TalkFragmentPack:
    return build_pack(
        CONFERENCE,
        TALK,
        [PackSource(DECK, [slide_draft(n, text) for n, text in enumerate(SLIDES, 1)])],
        [PackSource(NOTES, [note_draft(1, NOTE)])],
    )


def pack_blob(storage: Storage, value: TalkFragmentPack) -> ArtifactRef:
    """Пакет в виде выхода этапа pack — так его получают шаги сводки (ADR-013)."""
    meta = ArtifactMeta(
        stage="pack",
        stage_version="test",
        contract_version=CONTRACT_VERSION,
        cache_key="0" * 64,
        input_hashes=[],
        config_hash="0" * 64,
        produced_at=dt.datetime(2026, 11, 5, tzinfo=dt.UTC),
        stub=False,
    )
    data = PackArtifact(meta=meta, pack=value).model_dump_json().encode()
    return put_blob(storage, data, name="pack")


def fact(text: str, label: str, quote: str) -> dict[str, Any]:
    return {"text": text, "evidence": [{"label": label, "quote": quote}]}


FACTS: dict[str, Any] = {
    "title": "Кэширование запросов в распределённых базах данных",
    "speakers": ["Иванов И. И."],
    "affiliations": ["Институт примера"],
    "talk_type": "исследовательский",
    "problem": fact(
        "Снижение задержки чтения при неравномерной нагрузке",
        "S2",
        "снизить задержку чтения при неравномерной нагрузке",
    ),
    "methods": [
        fact(
            "Сравнение LRU и LFU на стенде из 4 узлов",
            "S3",
            "сравнение политик LRU и LFU на стенде из 4 узлов",
        )
    ],
    "results": [
        fact("LFU снизила задержку на 23 %", "S4", "политика LFU снизила задержку на 23 %")
    ],
    "conclusions": [
        fact(
            "LFU эффективнее LRU",
            "S5",
            "LFU эффективнее LRU при неравномерном распределении запросов",
        )
    ],
    "conclusions_absent_reason": None,
    "listener_remarks": [
        fact("Холодный старт не исследовался", "N1", "Докладчик ответил, что он не исследовался")
    ],
    "uncertain": [],
}


def line(text: str, *keys: str) -> dict[str, Any]:
    return {"text": text, "claim_ids": list(keys)}


SENTENCES = [
    line("В докладе рассмотрено снижение задержки чтения при неравномерной нагрузке.", "c1"),
    line("Автор сравнил политики LRU и LFU на стенде из 4 узлов.", "c2"),
    line("Показано, что политика LFU снизила задержку на 23 %.", "c3"),
    line("Сравнение проводилось при нагрузке 1000 запросов в секунду.", "c3"),
]
CONCLUSIONS = [line("LFU эффективнее LRU при неравномерном распределении запросов.", "c4")]


def summary_answer(
    sentences: list[dict[str, Any]] | None = None, conclusions: list[dict[str, Any]] | None = None
) -> str:
    return json.dumps(
        {
            "sentences": SENTENCES if sentences is None else sentences,
            "conclusions": CONCLUSIONS if conclusions is None else conclusions,
        },
        ensure_ascii=False,
    )


def facts_answer(**changes: Any) -> str:
    return json.dumps({**FACTS, **changes}, ensure_ascii=False)
