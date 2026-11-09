"""Фоновая обработка докладов в процессе приложения (ADR-014).

Доклад ставится в очередь полем talks.job = 'queued'. Один поток берёт доклады по одному
и проходит цепочку spec §6: разбор презентаций и заметок → пакет фрагментов → факты (LLM) →
сверка цитат → сводка (LLM). Результаты шагов кэшируются на томе (spec §3, п. 6), поэтому
повтор после сбоя или перепривязки не обращается к модели заново, если входы не изменились.
Обработка одного доклада за раз: GPU одна на всех, Ollama всё равно выполняет запросы
по очереди (ADR-010).
"""

import logging
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from rapporteur import db, model_choice
from rapporteur.contracts import (
    FactsArtifact,
    ModelProfile,
    NotesArtifact,
    SlidesArtifact,
    Summary,
    TalkFragmentPack,
    can_transition,
)
from rapporteur.contracts.enums import FileKind, TalkStatus
from rapporteur.errors import FailureCode, StageError
from rapporteur.ingest import notes as notes_parser
from rapporteur.ingest import slides as slides_parser
from rapporteur.ingest.limits import NotesConfig, PresentationConfig
from rapporteur.llm.client import LlmClient
from rapporteur.storage import Storage, blob_key, cache_key
from rapporteur.summarize import facts as facts_step
from rapporteur.summarize import summary as summary_step
from rapporteur.summarize.pack import PackSource, build_pack
from rapporteur.summarize.quotes import QuoteConfig
from rapporteur.summarize.verify import verify_facts

log = logging.getLogger("rapporteur.processing")

# Доклады с материалами, которые обработка берёт в работу при запуске.
READY_STATUSES = (TalkStatus.MATCHED.value, TalkStatus.STALE.value)
_RECORDINGS = {FileKind.AUDIO.value, FileKind.VIDEO.value}

ClientFactory = Callable[[ModelProfile], LlmClient]


def enqueue(conn: sqlite3.Connection, talk_ids: list[str]) -> None:
    """Повторная постановка во время обработки допустима: доклад пройдёт цепочку ещё раз."""
    conn.executemany(
        "UPDATE talks SET job = 'queued', step = NULL, error = NULL, queued_at = ? WHERE id = ?",
        [(db.now(), talk_id) for talk_id in talk_ids],
    )


def enqueue_ready(conn: sqlite3.Connection, conference_id: str) -> int:
    """Ставит доклады с привязанными материалами, которые ещё не обработаны или устарели.
    Доклад в работе ставится заново: его материалы могли измениться."""
    rows = [
        row["id"]
        for row in db.conference_talks(conn, conference_id)
        if row["status"] in READY_STATUSES and row["job"] != "queued"
    ]
    enqueue(conn, rows)
    return len(rows)


def slides_cache_key(sha256: str) -> str:
    return cache_key("slides", slides_parser.VERSION, sha256, PresentationConfig().model_dump())


def slide_image_key(sha256: str, slide_no: int) -> str:
    return f"slides/{slides_cache_key(sha256)}/slide-{slide_no:03}.png"


@dataclass(frozen=True)
class _Talk:
    id: str
    conference_id: str
    files: list[db.Row]


class Processor:
    def __init__(self, database: db.Database, storage: Storage, client: ClientFactory) -> None:
        self.database = database
        self.storage = storage
        self.client = client
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Доклады, прерванные остановкой приложения, обрабатываются заново.
        with self.database.tx() as conn:
            conn.execute("UPDATE talks SET job = 'queued', step = NULL WHERE job = 'running'")
        self._thread = threading.Thread(target=self._loop, name="processing", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                log.exception("Сбой цикла обработки")
                worked = False
            if not worked:
                self._wake.wait(timeout=5)
                self._wake.clear()

    def run_once(self) -> bool:
        """Обрабатывает следующий доклад из очереди; False, если очередь пуста."""
        talk = self._take()
        if talk is None:
            return False
        log.info("Обработка доклада %s", talk.id)
        try:
            summary, pack = self._process(talk)
        except StageError as error:
            log.warning("Доклад %s: %s", talk.id, error.code.value)
            self._finish(talk.id, "failed", f"{error.code.value}: {error.message}")
        except Exception:
            log.exception("Доклад %s: внутренняя ошибка", talk.id)
            self._finish(talk.id, "failed", "internal_error: внутренняя ошибка, см. журнал")
        else:
            self._save(talk.id, summary, pack)
        return True

    def _take(self) -> _Talk | None:
        with self.database.tx() as conn:
            row = db.one(
                conn,
                "SELECT talks.id, sections.conference_id FROM talks "
                "JOIN sections ON sections.id = talks.section_id "
                "WHERE talks.job = 'queued' ORDER BY talks.queued_at LIMIT 1",
            )
            if row is None:
                return None
            conn.execute(
                "UPDATE talks SET job = 'running', step = 'parse', error = NULL WHERE id = ?",
                (row["id"],),
            )
            files = conn.execute(
                "SELECT * FROM files WHERE talk_id = ? AND confirmed = 1 ORDER BY relative_path",
                (row["id"],),
            ).fetchall()
        return _Talk(row["id"], row["conference_id"], files)

    def _step(self, talk_id: str, step: str) -> None:
        with self.database.tx() as conn:
            conn.execute(
                "UPDATE talks SET step = ? WHERE id = ? AND job = 'running'", (step, talk_id)
            )

    def _finish(self, talk_id: str, job: str, error: str | None = None) -> None:
        # Если доклад поставили заново во время обработки, он остаётся в очереди.
        with self.database.tx() as conn:
            conn.execute(
                "UPDATE talks SET job = ?, step = NULL, error = ? WHERE id = ? AND job = 'running'",
                (job, error, talk_id),
            )

    def _process(self, talk: _Talk) -> tuple[Summary, TalkFragmentPack]:
        pack = self._pack(talk)
        # Модель — выбранная на странице «Управление» или профиль по умолчанию.
        with self.database.tx() as conn:
            profile = model_choice.active_profile(conn)

        self._step(talk.id, "facts")
        facts_config = facts_step.FactsConfig(profile=profile)
        facts_key = cache_key(
            "facts", facts_step.VERSION, pack.model_dump(mode="json"), facts_config.model_dump()
        )
        facts = self.storage.cached(
            f"cache/facts/{facts_key}.json",
            FactsArtifact,
            lambda: facts_step.extract_facts(self.client(profile), pack, facts_config),
        )

        self._step(talk.id, "verify")
        claims = verify_facts(facts.facts, pack, QuoteConfig(), id_seed=facts_key)

        self._step(talk.id, "summary")
        summary_config = summary_step.SummaryConfig(profile=profile)
        summary_key = cache_key(
            "summary", summary_step.VERSION, facts_key, summary_config.model_dump()
        )

        def write() -> Summary:
            written = summary_step.write_summary(
                self.client(profile), summary_config, facts.facts.title, claims, pack
            )
            return summary_step.make_summary(
                summary_key,
                pack,
                facts,
                claims,
                written,
                profile=profile.name,
                prompt_version=profile.prompt_version,
            )

        summary = self.storage.cached(f"cache/summary/{summary_key}.json", Summary, write)
        return summary, pack

    def _pack(self, talk: _Talk) -> TalkFragmentPack:
        presentations: list[PackSource] = []
        notes: list[PackSource] = []
        recordings = 0
        for file in talk.files:
            source = UUID(file["id"])
            if file["kind"] == FileKind.PRESENTATION.value:
                presentations.append(PackSource(source, self._slides(file["sha256"]).fragments))
            elif file["kind"] == FileKind.NOTES.value:
                notes.append(PackSource(source, self._notes(file["sha256"]).fragments))
            elif file["kind"] in _RECORDINGS:
                # Распознавание речи и разбиение записей — Ф2–Ф3 (spec §6.3–6.5).
                recordings += 1
        pack = build_pack(UUID(talk.conference_id), UUID(talk.id), presentations, notes)
        if not pack.fragments:
            message = "В материалах доклада нет текста"
            if recordings:
                message += ": распознавание записей появится в Ф2"
            raise StageError(FailureCode.NO_SOURCES, message)
        return pack

    def _slides(self, sha: str) -> SlidesArtifact:
        key = slides_cache_key(sha)

        def parse() -> SlidesArtifact:
            with tempfile.TemporaryDirectory(prefix="slides-") as work:
                data = self.storage.get(blob_key(sha))
                return slides_parser.parse_slides(
                    data, sha, self.storage, f"slides/{key}", Path(work)
                )

        return self.storage.cached(f"cache/slides/{key}.json", SlidesArtifact, parse)

    def _notes(self, sha: str) -> NotesArtifact:
        config = NotesConfig()
        key = cache_key("notes", notes_parser.VERSION, sha, config.model_dump())
        return self.storage.cached(
            f"cache/notes/{key}.json",
            NotesArtifact,
            lambda: notes_parser.parse_notes(self.storage.get(blob_key(sha)), sha, config),
        )

    def _save(self, talk_id: str, summary: Summary, pack: TalkFragmentPack) -> None:
        """Новая машинная сводка становится очередной версией и требует проверки человеком.
        Тот же результат из кэша повторно не записывается."""
        with self.database.tx() as conn:
            talk = db.one(conn, "SELECT status FROM talks WHERE id = ?", talk_id)
            if talk is None:
                return  # Доклад удалили, пока шла обработка.
            known = db.one(
                conn,
                "SELECT 1 FROM summaries WHERE talk_id = ? AND json_extract(summary, '$.id') = ?",
                talk_id,
                str(summary.id),
            )
            if known is None:
                number = conn.execute(
                    "SELECT coalesce(max(number), 0) + 1 FROM summaries WHERE talk_id = ?",
                    (talk_id,),
                ).fetchone()[0]
                stored = summary.model_copy(update={"version": number})
                conn.execute(
                    "INSERT INTO summaries (talk_id, number, summary, pack, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        talk_id,
                        number,
                        stored.model_dump_json(),
                        pack.model_dump_json(),
                        stored.created_at.isoformat(),
                    ),
                )
                if can_transition(TalkStatus(talk["status"]), TalkStatus.PROCESSED):
                    conn.execute(
                        "UPDATE talks SET status = ? WHERE id = ?",
                        (TalkStatus.PROCESSED.value, talk_id),
                    )
            conn.execute(
                "UPDATE talks SET job = 'done', step = NULL, error = NULL "
                "WHERE id = ? AND job = 'running'",
                (talk_id,),
            )
