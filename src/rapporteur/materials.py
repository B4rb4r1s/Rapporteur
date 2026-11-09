"""Реестр материалов, подсказки привязок и их подтверждение (spec §4.2, проверка 1)."""

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import PurePosixPath
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from rapporteur import db
from rapporteur.contracts import ConferenceRegistry, SourceFile, Speaker, source_file_id
from rapporteur.contracts.artifacts import InventoryItem, TitleCandidates
from rapporteur.contracts.enums import FileKind, PersonRole, TalkStatus
from rapporteur.contracts.registry import (
    RegistryAudio,
    RegistryConference,
    RegistrySection,
    RegistrySources,
    RegistryTalk,
)
from rapporteur.errors import NotFoundError, UserError
from rapporteur.ingest.inventory import PPTX, Inspection

_TALK_FOLDER = re.compile(r"^([0-9]{1,6})[_. -]+(.+)$")
_RECORDINGS = {FileKind.AUDIO, FileKind.VIDEO}
PRIMARY_SOURCES = {FileKind.PRESENTATION, *_RECORDINGS}
_REVIEW_STATUSES = {TalkStatus.DRAFT, TalkStatus.MATCHED, TalkStatus.INSUFFICIENT_DATA}
MAX_SECTIONS = 5
DEFAULT_SECTION = "Основная секция"


class BindingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file_id: str
    talk_id: str | None


class BindingReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: list[BindingChoice]


class Binding(BaseModel):
    talk_id: str | None
    confirmed: bool
    confidence: float | None


class Material(BaseModel):
    source: SourceFile
    relative_path: str
    title_candidates: TitleCandidates | None
    warning: str | None
    binding: Binding
    suggested_talk_id: str | None = None
    confidence: float | None = None
    alternate_of: str | None = None


class ReviewTalk(BaseModel):
    id: str
    section_id: str
    section: str
    order: int
    title: str | None
    status: TalkStatus


class ReviewSection(BaseModel):
    id: str
    title: str


class Materials(BaseModel):
    processing_started: bool
    files: list[Material]
    sections: list[ReviewSection]
    talks: list[ReviewTalk]


def safe_path(value: str) -> str:
    parts = value.split("/")
    if (
        any(not part.strip() or part in {".", ".."} for part in parts)
        or re.search(r"[\\:\x00-\x1f\x7f]", value)
        or len(parts) > 20
    ):
        raise UserError("Недопустимый путь материала")
    return value


def folder(path: str) -> tuple[str, str, int | None, str] | None:
    """Папка доклада: «Секция/01_Иванов/» или «Иванов/». Без номера доклад получает
    следующий порядковый номер в секции, то есть по порядку загрузки папок."""
    parts = path.split("/")[:-1]
    if not parts or any(part.casefold() == "_общее" for part in parts):
        return None
    match = _TALK_FOLDER.fullmatch(parts[-1])
    return (
        parts[-2].strip() if len(parts) > 1 else DEFAULT_SECTION,
        "/".join(parts),
        max(1, int(match[1])) if match else None,
        (match[2] if match else parts[-1]).strip(),
    )


def require_conference(conn: sqlite3.Connection, conference_id: str) -> db.Row:
    row = db.one(conn, "SELECT * FROM conferences WHERE id = ?", conference_id)
    if row is None:
        raise NotFoundError("Конференция не найдена")
    return row


def add_file(
    conn: sqlite3.Connection,
    conference_id: str,
    path: str,
    item: InventoryItem,
    inspection: Inspection,
) -> tuple[SourceFile, bool]:
    """Файл и, если он лежит в папке доклада, секция и доклад реестра. Повтор того же
    содержимого не создаёт новую запись."""
    require_conference(conn, conference_id)
    known = db.one(
        conn,
        "SELECT * FROM files WHERE conference_id = ? AND sha256 = ?",
        conference_id,
        item.sha256,
    )
    if known is not None:
        return db.source_file(known), False
    if db.one(
        conn,
        "SELECT id FROM files WHERE conference_id = ? AND relative_path = ?",
        conference_id,
        path,
    ):
        raise UserError("По этому пути уже загружен другой файл", 409)
    # Тот же id вычисляет пакет фрагментов: фрагменты ссылаются на эту запись (ADR-013).
    file_id = str(source_file_id(UUID(conference_id), item.sha256))
    conn.execute(
        "INSERT INTO files (id, conference_id, sha256, original_name, relative_path, kind, "
        "media_type, size_bytes, duration_ms, encoding, title_candidates, warning) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            file_id,
            conference_id,
            item.sha256,
            item.original_name,
            path,
            item.kind.value,
            item.media_type,
            item.size_bytes,
            item.duration_ms,
            item.encoding,
            inspection.title.model_dump_json() if inspection.title else None,
            inspection.warning,
        ),
    )
    if item.kind is not FileKind.PROGRAM and (location := folder(path)) is not None:
        _register_folder(conn, conference_id, location, inspection.title)
    row = db.one(conn, "SELECT * FROM files WHERE id = ?", file_id)
    assert row is not None
    return db.source_file(row), True


def _register_folder(
    conn: sqlite3.Connection,
    conference_id: str,
    location: tuple[str, str, int | None, str],
    title: TitleCandidates | None,
) -> None:
    section_name, import_folder, order, fallback = location
    sections = _sections(conn, conference_id)
    section = next((row for row in sections if _norm(row["title"]) == _norm(section_name)), None)
    if section is None:
        if len(sections) >= MAX_SECTIONS:
            return
        section_id = add_section(conn, conference_id, section_name)
    else:
        section_id = section["id"]
    rows = conn.execute("SELECT * FROM talks WHERE section_id = ?", (section_id,)).fetchall()
    if any(row["import_folder"] == import_folder for row in rows):
        return
    if order is None:
        order = max((row["position"] for row in rows), default=0) + 1
    elif any(row["position"] == order for row in rows):
        return
    conn.execute(
        "INSERT INTO talks (id, section_id, position, title, status, import_folder) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            db.new_id(),
            section_id,
            order,
            title.title if title and title.title else fallback,
            TalkStatus.DRAFT.value,
            import_folder,
        ),
    )


def add_section(conn: sqlite3.Connection, conference_id: str, title: str) -> str:
    position = conn.execute(
        "SELECT coalesce(max(position), 0) + 1 FROM sections WHERE conference_id = ?",
        (conference_id,),
    ).fetchone()[0]
    section_id = db.new_id()
    conn.execute(
        "INSERT INTO sections (id, conference_id, title, position) VALUES (?, ?, ?, ?)",
        (section_id, conference_id, title, position),
    )
    return section_id


def add_talk(
    conn: sqlite3.Connection, conference_id: str, title: str, section_id: str | None
) -> str:
    """Доклад вручную, в конец секции. Без секций создаётся основная."""
    require_conference(conn, conference_id)
    sections = _sections(conn, conference_id)
    if section_id is None:
        section_id = (
            sections[0]["id"] if sections else add_section(conn, conference_id, DEFAULT_SECTION)
        )
    elif all(row["id"] != section_id for row in sections):
        raise NotFoundError("Секция не найдена")
    position = conn.execute(
        "SELECT coalesce(max(position), 0) + 1 FROM talks WHERE section_id = ?", (section_id,)
    ).fetchone()[0]
    talk_id = db.new_id()
    conn.execute(
        "INSERT INTO talks (id, section_id, position, title, status) VALUES (?, ?, ?, ?, ?)",
        (talk_id, section_id, position, title, TalkStatus.DRAFT.value),
    )
    return talk_id


def _sections(conn: sqlite3.Connection, conference_id: str) -> list[db.Row]:
    return conn.execute(
        "SELECT * FROM sections WHERE conference_id = ? ORDER BY position, id", (conference_id,)
    ).fetchall()


def _files(conn: sqlite3.Connection, conference_id: str) -> list[Material]:
    rows = conn.execute(
        "SELECT * FROM files WHERE conference_id = ? ORDER BY relative_path, id", (conference_id,)
    ).fetchall()
    return [
        Material(
            source=db.source_file(row),
            relative_path=row["relative_path"],
            title_candidates=TitleCandidates.model_validate_json(row["title_candidates"])
            if row["title_candidates"]
            else None,
            warning=row["warning"],
            binding=Binding(
                talk_id=row["talk_id"],
                confirmed=bool(row["confirmed"]),
                confidence=row["confidence"],
            ),
        )
        for row in rows
    ]


def _norm(value: str | None) -> str:
    return " ".join(re.findall(r"[^\W_]+", (value or "").casefold().replace("ё", "е")))


def _speakers(row: db.Row, role: PersonRole = PersonRole.SPEAKER) -> list[Speaker]:
    return [
        speaker
        for speaker in (Speaker.model_validate(item) for item in json.loads(row["speakers"]))
        if speaker.role is role
    ]


def _suggest(file: Material, talks: list[db.Row]) -> tuple[str | None, float | None]:
    if (
        file.source.kind is FileKind.PROGRAM
        or "_общее" in file.relative_path.casefold().split("/")[:-1]
    ):
        return None, None
    location = folder(file.relative_path)
    stem = _norm(PurePosixPath(file.relative_path).stem)
    candidate = file.title_candidates
    title = _norm(candidate.title if candidate else None)
    names = {_norm(name) for name in candidate.speakers} if candidate else set()
    scores: list[tuple[float, str]] = []
    for row in talks:
        expected = _norm(row["title"])
        expected_names = {_norm(speaker.name) for speaker in _speakers(row)}
        if expected and title == expected:
            score = 0.98
        elif location and row["import_folder"] == location[1]:
            score = 0.90
        elif names & expected_names:
            score = 0.85
        elif (expected and stem == expected) or any(
            name.split()[0] in stem.split() for name in expected_names if name
        ):
            score = 0.75
        elif (
            location
            and row["position"] == location[2]
            and _norm(row["section_title"]) == _norm(location[0])
        ):
            score = 0.60
        else:
            continue
        scores.append((score, row["id"]))
    scores.sort(reverse=True)
    if scores and (len(scores) == 1 or scores[0][0] > scores[1][0]):
        score, talk_id = scores[0]
        return talk_id, score
    return None, None


def review(conn: sqlite3.Connection, conference_id: str) -> Materials:
    conference = require_conference(conn, conference_id)
    talks = db.conference_talks(conn, conference_id)
    files = _files(conn, conference_id)
    for file in files:
        file.suggested_talk_id, file.confidence = _suggest(file, talks)
    presentations = [file for file in files if file.source.kind is FileKind.PRESENTATION]
    pptx = [file for file in presentations if file.source.media_type == PPTX]
    for file in presentations:
        if file.source.media_type != "application/pdf":
            continue
        matches = [primary for primary in pptx if _same_presentation(primary, file)]
        if len(matches) == 1:
            file.alternate_of = str(matches[0].source.id)
    return Materials(
        processing_started=bool(conference["processing_started"]),
        files=files,
        sections=[
            ReviewSection(id=row["id"], title=row["title"])
            for row in _sections(conn, conference_id)
        ],
        talks=[
            ReviewTalk(
                id=row["id"],
                section_id=row["section_id"],
                section=row["section_title"],
                title=row["title"],
                order=row["position"],
                status=row["status"],
            )
            for row in talks
        ],
    )


def _same_presentation(pptx: Material, pdf: Material) -> bool:
    """PDF-копия презентации: та же папка и имя или то же название и докладчик."""
    p, q = PurePosixPath(pptx.relative_path), PurePosixPath(pdf.relative_path)
    if p.parent == q.parent and _norm(p.stem) and _norm(p.stem) == _norm(q.stem):
        return True
    a, b = pptx.title_candidates, pdf.title_candidates
    return bool(
        a
        and b
        and _norm(a.title)
        and _norm(a.title) == _norm(b.title)
        and (
            {_norm(name) for name in a.speakers} & {_norm(name) for name in b.speakers}
            or (
                pptx.suggested_talk_id is not None
                and pptx.suggested_talk_id == pdf.suggested_talk_id
            )
        )
    )


def confirm(conn: sqlite3.Connection, conference_id: str, data: BindingReview) -> None:
    """Решение по каждому файлу. Статусы докладов пересчитываются: с презентацией или записью
    доклад готов к обработке, а у обработанного доклада смена материалов делает сводку
    устаревшей."""
    talks = {row["id"]: row for row in db.conference_talks(conn, conference_id)}
    files = {str(file.source.id): file for file in review(conn, conference_id).files}
    choices = {item.source_file_id: item.talk_id for item in data.bindings}
    if len(choices) != len(data.bindings) or choices.keys() != files.keys():
        raise UserError("Нужно указать решение для каждого файла")
    for file_id, talk_id in choices.items():
        if talk_id is not None and (
            talk_id not in talks or files[file_id].source.kind is FileKind.PROGRAM
        ):
            raise UserError("Недопустимая привязка файла к докладу")
    by_talk: dict[str, list[Material]] = defaultdict(list)
    changed: set[str] = set()
    for file_id, talk_id in choices.items():
        file = files[file_id]
        previous = file.binding
        if talk_id is not None:
            by_talk[talk_id].append(file)
        if previous.confirmed and previous.talk_id == talk_id:
            continue
        changed.update(value for value in (previous.talk_id, talk_id) if value is not None)
        confidence = (
            file.confidence if talk_id is not None and talk_id == file.suggested_talk_id else None
        )
        conn.execute(
            "UPDATE files SET talk_id = ?, confirmed = 1, confidence = ? WHERE id = ?",
            (talk_id, confidence, file_id),
        )
    for talk_id, row in talks.items():
        current = TalkStatus(row["status"])
        has_source = any(file.source.kind in PRIMARY_SOURCES for file in by_talk[talk_id])
        target = current
        if current in _REVIEW_STATUSES:
            if has_source:
                target = TalkStatus.MATCHED
            else:
                target = TalkStatus.INSUFFICIENT_DATA if row["title"] else TalkStatus.DRAFT
        elif current in {TalkStatus.PROCESSED, TalkStatus.REVIEWED} and talk_id in changed:
            target = TalkStatus.STALE
        if target != current:
            conn.execute("UPDATE talks SET status = ? WHERE id = ?", (target.value, talk_id))


def registry(conn: sqlite3.Connection, conference_id: str) -> ConferenceRegistry:
    """Реестр конференции в формате conference.yaml (spec §7.4)."""
    conference = require_conference(conn, conference_id)
    talks = db.conference_talks(conn, conference_id)
    sources: dict[str, RegistrySources] = defaultdict(RegistrySources)
    for file in _files(conn, conference_id):
        if not file.binding.confirmed or file.binding.talk_id is None:
            continue
        destination = sources[file.binding.talk_id]
        if file.source.kind is FileKind.PRESENTATION:
            destination.slides.append(file.relative_path)
        elif file.source.kind is FileKind.NOTES:
            destination.notes.append(file.relative_path)
        elif file.source.kind in _RECORDINGS and file.source.duration_ms is not None:
            seconds = (file.source.duration_ms + 999) // 1000
            destination.audio.append(
                RegistryAudio(
                    file=file.relative_path,
                    start="00:00:00",
                    end=f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}",
                )
            )
    return ConferenceRegistry(
        conference=RegistryConference(
            title=conference["title"],
            place=conference["place"],
            dates=list(
                dict.fromkeys(
                    date for date in (conference["start_date"], conference["end_date"]) if date
                )
            ),
        ),
        sections=[
            RegistrySection(id=row["id"], title=row["title"], date=row["date"])
            for row in _sections(conn, conference_id)
        ],
        talks=[
            RegistryTalk(
                id=row["id"],
                section=row["section_id"],
                order=row["position"],
                title=row["title"],
                date=row["date"] or row["section_date"],
                speakers=[speaker.name for speaker in _speakers(row)],
                coauthors=[speaker.name for speaker in _speakers(row, PersonRole.COAUTHOR)],
                affiliations=list(
                    dict.fromkeys(
                        speaker.affiliation
                        for speaker in _speakers(row) + _speakers(row, PersonRole.COAUTHOR)
                        if speaker.affiliation
                    )
                ),
                sources=sources[row["id"]],
                status=row["status"],
            )
            for row in talks
        ],
    )
