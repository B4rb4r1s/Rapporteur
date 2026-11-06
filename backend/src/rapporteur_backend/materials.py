"""Реестр материалов, подсказки и подтверждение привязок (P1-05)."""

import re
from collections import defaultdict
from pathlib import PurePosixPath
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, RowMapping

from rapporteur_backend import store
from rapporteur_backend import tables as t
from rapporteur_backend.db import LOCAL_USER_ID
from rapporteur_backend.errors import ApiRequestError
from rapporteur_contracts import FileBinding, SourceFile, source_file_id
from rapporteur_contracts.artifacts import InventoryItem, TitleCandidates
from rapporteur_contracts.enums import ApiErrorCode, FileKind, PersonRole, TalkStatus
from rapporteur_contracts.registry import (
    ConferenceRegistry,
    RegistryAudio,
    RegistryConference,
    RegistrySection,
    RegistrySources,
    RegistryTalk,
)
from rapporteur_pipeline.ingest.inventory import PPTX, Inspection

_TALK_FOLDER = re.compile(r"^([0-9]{1,6})[_. -]+(.+)$")
_RECORDINGS = {FileKind.AUDIO, FileKind.VIDEO}
_PRIMARY_SOURCES = {FileKind.PRESENTATION, *_RECORDINGS}
_REVIEW_STATUSES = {TalkStatus.DRAFT, TalkStatus.MATCHED, TalkStatus.INSUFFICIENT_DATA}


class BindingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file_id: UUID
    talk_id: UUID | None


class BindingReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: list[BindingChoice]


class Material(BaseModel):
    source: SourceFile
    relative_path: str
    title_candidates: TitleCandidates | None
    warning: str | None
    binding: FileBinding
    suggested_talk_id: UUID | None = None
    confidence: float | None = None
    alternate_of: UUID | None = None


class ReviewTalk(BaseModel):
    id: UUID
    section_id: UUID
    section: str
    order: int
    title: str | None
    status: TalkStatus


class ReviewSection(BaseModel):
    id: UUID
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
        raise ApiRequestError(ApiErrorCode.VALIDATION_ERROR, "Недопустимый путь материала")
    return value


def folder(path: str) -> tuple[str, str, int | None, str] | None:
    """Папка доклада: «Секция/01_Иванов/» или «Иванов/». Без номера доклад получает
    следующий порядковый номер в секции, то есть по порядку загрузки папок."""
    parts = path.split("/")[:-1]
    if not parts or any(part.casefold() == "_общее" for part in parts):
        return None
    match = _TALK_FOLDER.fullmatch(parts[-1])
    return (
        parts[-2].strip() if len(parts) > 1 else "Основная секция",
        "/".join(parts),
        max(1, int(match[1])) if match else None,
        (match[2] if match else parts[-1]).strip(),
    )


def add_file(
    conn: Connection, conference_id: UUID, path: str, item: InventoryItem, inspection: Inspection
) -> tuple[SourceFile, bool]:
    conference = store.require(conn, t.conferences, conference_id, lock=True)
    files = t.source_files
    known = (
        conn.execute(
            sa.select(files).where(
                files.c.conference_id == conference_id, files.c.sha256 == item.sha256
            )
        )
        .mappings()
        .one_or_none()
    )
    if known is not None:
        return _source(known), False
    if conn.scalar(
        sa.select(files.c.id).where(
            files.c.conference_id == conference_id, files.c.relative_path == path
        )
    ):
        raise ApiRequestError(
            ApiErrorCode.VERSION_CONFLICT, "По этому пути уже загружен другой файл"
        )
    row = store.create(
        conn,
        files,
        {
            **item.model_dump(mode="json"),
            "conference_id": conference_id,
            "relative_path": path,
            "title_candidates": inspection.title.model_dump(mode="json")
            if inspection.title
            else None,
            "warning": inspection.warning,
        },
        # Тот же id вычисляет этап pack: фрагменты пакета ссылаются на эту запись (ADR-013).
        entity_id=source_file_id(conference_id, item.sha256),
    )
    conn.execute(
        sa.insert(t.file_bindings).values(id=uuid4(), source_file_id=row["id"], confirmed=False)
    )
    if item.kind is not FileKind.PROGRAM and (location := folder(path)) is not None:
        _register_folder(conn, conference_id, location, inspection.title)
    _touch_conference(conn, conference)
    return _source(row), True


def _source(row: RowMapping) -> SourceFile:
    return SourceFile.model_validate({key: row[key] for key in SourceFile.model_fields})


def _register_folder(
    conn: Connection,
    conference_id: UUID,
    location: tuple[str, str, int | None, str],
    title: TitleCandidates | None,
) -> None:
    section_name, import_folder, order, fallback = location
    sections = _sections(conn, conference_id)
    section = next((row for row in sections if _norm(row["title"]) == _norm(section_name)), None)
    if section is None:
        if len(sections) >= 5:
            return
        section = store.create(
            conn,
            t.sections,
            {
                "conference_id": conference_id,
                "title": section_name,
                "order": max((row["order"] for row in sections), default=0) + 1,
            },
        )
    talks = t.talks
    rows = (
        conn.execute(sa.select(talks).where(talks.c.section_id == section["id"])).mappings().all()
    )
    if any(row["import_folder"] == import_folder for row in rows):
        return
    if order is None:
        order = max((row["order"] for row in rows), default=0) + 1
    elif any(row["order"] == order for row in rows):
        return
    store.create(
        conn,
        talks,
        {
            "section_id": section["id"],
            "order": order,
            "title": title.title if title and title.title else fallback,
            "status": TalkStatus.DRAFT.value,
            "import_folder": import_folder,
        },
    )


def _sections(conn: Connection, conference_id: UUID) -> list[RowMapping]:
    sections = t.sections
    return list(
        conn.execute(
            sa.select(sections)
            .where(sections.c.conference_id == conference_id)
            .order_by(sections.c.order, sections.c.id)
        ).mappings()
    )


def _talks(conn: Connection, conference_id: UUID, *, lock: bool = False) -> list[RowMapping]:
    talks, sections = t.talks, t.sections
    statement = (
        sa.select(
            talks, sections.c.title.label("section_title"), sections.c.date.label("section_date")
        )
        .join(sections)
        .where(sections.c.conference_id == conference_id)
        .order_by(sections.c.order, sections.c.id, talks.c.order, talks.c.id)
    )
    if lock:
        statement = statement.with_for_update(of=talks)
    return list(conn.execute(statement).mappings())


def _files(conn: Connection, conference_id: UUID) -> list[Material]:
    files, bindings = t.source_files, t.file_bindings
    return [
        Material(
            source=_source(row),
            relative_path=row["relative_path"],
            title_candidates=row["title_candidates"],
            warning=row["warning"],
            binding=FileBinding(
                id=row["binding_id"],
                source_file_id=row["id"],
                talk_id=row["talk_id"],
                confirmed=row["confirmed"],
                confidence=row["confidence"],
            ),
        )
        for row in conn.execute(
            sa.select(
                files,
                bindings.c.id.label("binding_id"),
                bindings.c.talk_id,
                bindings.c.confirmed,
                bindings.c.confidence,
            )
            .join(bindings)
            .where(files.c.conference_id == conference_id)
            .order_by(files.c.relative_path, files.c.id)
        ).mappings()
    ]


def _norm(value: str | None) -> str:
    return " ".join(re.findall(r"[^\W_]+", (value or "").casefold().replace("ё", "е")))


def _people(
    conn: Connection, talk_ids: list[UUID], role: PersonRole
) -> dict[UUID, list[tuple[str, str | None]]]:
    result: dict[UUID, list[tuple[str, str | None]]] = defaultdict(list)
    people, participants = t.people, t.participants
    statement = (
        sa.select(participants.c.talk_id, people.c.full_name, people.c.affiliation)
        .join(people)
        .where(participants.c.talk_id.in_(talk_ids), participants.c.role == role.value)
        .order_by(participants.c.order)
    )
    for talk_id, name, affiliation in conn.execute(statement):
        result[talk_id].append((name, affiliation))
    return result


def _suggest(
    file: Material, talks: list[RowMapping], speakers: dict[UUID, list[tuple[str, str | None]]]
) -> tuple[UUID | None, float | None]:
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
    scores: list[tuple[float, UUID]] = []
    for row in talks:
        expected = _norm(row["title"])
        expected_names = {_norm(name) for name, _ in speakers.get(row["id"], [])}
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
            and row["order"] == location[2]
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


def review(conn: Connection, conference_id: UUID) -> Materials:
    talks = _talks(conn, conference_id)
    files = _files(conn, conference_id)
    speakers = _people(conn, [row["id"] for row in talks], PersonRole.SPEAKER)
    for file in files:
        file.suggested_talk_id, file.confidence = _suggest(file, talks, speakers)
    presentations = [file for file in files if file.source.kind is FileKind.PRESENTATION]
    pptx = [file for file in presentations if file.source.media_type == PPTX]
    for file in presentations:
        if file.source.media_type != "application/pdf":
            continue
        matches = [primary for primary in pptx if _same_presentation(primary, file)]
        if len(matches) == 1:
            file.alternate_of = matches[0].source.id
    return Materials(
        processing_started=bool(
            conn.scalar(
                sa.select(t.conferences.c.processing_started).where(
                    t.conferences.c.id == conference_id
                )
            )
        ),
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
                order=row["order"],
                status=row["status"],
            )
            for row in talks
        ],
    )


def _same_presentation(pptx: Material, pdf: Material) -> bool:
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


def _touch_conference(conn: Connection, conference: RowMapping) -> int:
    version = int(conference["version"]) + 1
    conn.execute(
        sa.update(t.conferences)
        .where(t.conferences.c.id == conference["id"])
        .values(version=version, edited_by=LOCAL_USER_ID)
    )
    return version


def confirm(conn: Connection, conference_id: UUID, version: int, data: BindingReview) -> int:
    conference = store.require(conn, t.conferences, conference_id, lock=True)
    store.check_version(conference, version)
    talks = {row["id"]: row for row in _talks(conn, conference_id, lock=True)}
    files = {file.source.id: file for file in review(conn, conference_id).files}
    choices = {item.source_file_id: item.talk_id for item in data.bindings}
    if len(choices) != len(data.bindings) or choices.keys() != files.keys():
        raise ApiRequestError(
            ApiErrorCode.VALIDATION_ERROR, "Нужно указать решение для каждого файла"
        )
    for file_id, talk_id in choices.items():
        if talk_id is not None and (
            talk_id not in talks or files[file_id].source.kind is FileKind.PROGRAM
        ):
            raise ApiRequestError(
                ApiErrorCode.VALIDATION_ERROR, "Недопустимая привязка файла к докладу"
            )
    by_talk: dict[UUID, list[Material]] = defaultdict(list)
    changed_talks: set[UUID] = set()
    for file_id, talk_id in choices.items():
        file = files[file_id]
        previous = file.binding
        if talk_id is not None:
            by_talk[talk_id].append(file)
        if previous.confirmed and previous.talk_id == talk_id:
            continue
        changed_talks.update(value for value in (previous.talk_id, talk_id) if value is not None)
        conn.execute(
            sa.update(t.file_bindings)
            .where(t.file_bindings.c.id == previous.id)
            .values(
                confirmed=True,
                talk_id=talk_id,
                confidence=file.confidence
                if talk_id is not None and talk_id == file.suggested_talk_id
                else None,
            )
        )
    for talk_id, row in talks.items():
        current = TalkStatus(row["status"])
        has_source = any(file.source.kind in _PRIMARY_SOURCES for file in by_talk[talk_id])
        target = current
        if current in _REVIEW_STATUSES:
            target = (
                TalkStatus.MATCHED
                if has_source
                else (TalkStatus.INSUFFICIENT_DATA if row["title"] else TalkStatus.DRAFT)
            )
        elif current in {TalkStatus.PROCESSED, TalkStatus.REVIEWED} and talk_id in changed_talks:
            target = TalkStatus.STALE
        if target != current or talk_id in changed_talks:
            store.replace(conn, t.talks, talk_id, row["version"], {"status": target.value})
    return _touch_conference(conn, conference)


def registry(conn: Connection, conference: RowMapping) -> ConferenceRegistry:
    conference_id = conference["id"]
    sections = _sections(conn, conference_id)
    talks = _talks(conn, conference_id)
    talk_ids = [row["id"] for row in talks]
    speakers = _people(conn, talk_ids, PersonRole.SPEAKER)
    coauthors = _people(conn, talk_ids, PersonRole.COAUTHOR)
    sources: dict[UUID, RegistrySources] = defaultdict(RegistrySources)
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
            RegistrySection(id=str(row["id"]), title=row["title"], date=row["date"])
            for row in sections
        ],
        talks=[
            RegistryTalk(
                id=str(row["id"]),
                section=str(row["section_id"]),
                order=row["order"],
                title=row["title"],
                date=row["date"] or row["section_date"],
                speakers=[name for name, _ in speakers.get(row["id"], [])],
                coauthors=[name for name, _ in coauthors.get(row["id"], [])],
                affiliations=list(
                    dict.fromkeys(
                        affiliation
                        for _, affiliation in speakers[row["id"]] + coauthors[row["id"]]
                        if affiliation
                    )
                ),
                sources=sources[row["id"]],
                status=row["status"],
                confidence=row["match_confidence"],
            )
            for row in talks
        ],
    )
