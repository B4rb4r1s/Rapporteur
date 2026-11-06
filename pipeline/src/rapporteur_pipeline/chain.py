"""Цепочка «папка → справка» без веба и БД (ADR-007), вертикальный срез Ф1.

Разбор файлов, пакет фрагментов каждого доклада, факты, сверка цитат, сводка и сборка справки.
До очереди P1-02 эта цепочка заменяет оркестратор. Каждый этап кэшируется по ключу ADR-005:
повторный запуск после правки реестра не обращается к модели заново.

Доклады берутся из реестра (ConferenceRegistry, YAML). Если реестра нет, пишется черновик по
титульным слайдам: презентации с одинаковым названием считаются одним докладом. Черновик надо
проверить: название и докладчики в справку идут из реестра (ADR-008).
"""

import datetime as dt
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from rapporteur_contracts import (
    ArtifactRef,
    Conference,
    ConferenceRegistry,
    ModelProfile,
    NotesArtifact,
    Person,
    Report,
    ReportArtifact,
    ReportRequest,
    Section,
    SlidesArtifact,
    StageInputs,
    Summary,
    SummaryArtifact,
    Talk,
    VerifiedClaimsArtifact,
)
from rapporteur_contracts.entities import TalkParticipant
from rapporteur_contracts.enums import ClaimStatus, PersonRole, TalkStatus
from rapporteur_contracts.registry import (
    RegistryConference,
    RegistrySection,
    RegistrySources,
    RegistryTalk,
)
from rapporteur_pipeline.ingest.notes import NotesStage
from rapporteur_pipeline.ingest.slides import SlidesStage
from rapporteur_pipeline.llm.client import LlmClient
from rapporteur_pipeline.report.assemble import ReportError, ReportSources, select_talks
from rapporteur_pipeline.report.stage import REQUEST_INPUT, ReportConfig, ReportStage
from rapporteur_pipeline.report.template import TemplateError, load_template
from rapporteur_pipeline.stage import (
    SOURCE_INPUT,
    Stage,
    StageContext,
    StageError,
    cache_key,
    config_hash,
)
from rapporteur_pipeline.storage import (
    DirectoryStorage,
    Storage,
    artifact_key,
    blob_key,
    sha256_hex,
)
from rapporteur_pipeline.stubs import StubFactsStage, StubSummaryStage
from rapporteur_pipeline.summarize.facts import FactsConfig, FactsStage
from rapporteur_pipeline.summarize.pack import PackConfig, PackStage
from rapporteur_pipeline.summarize.quotes import QuoteConfig
from rapporteur_pipeline.summarize.summary import SummaryConfig, SummaryStage
from rapporteur_pipeline.summarize.verify import VerifyStage

PRESENTATION_SUFFIXES = frozenset({".pptx", ".pdf", ".ppt"})
NOTES_SUFFIXES = frozenset({".txt", ".md"})
STARTER_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "starter"
REGISTRY_FILE = "registry.yaml"
DRAFT_FILE = "registry-draft.yaml"

_NAMESPACE = uuid.UUID("4c8f1e2a-7b3d-4a6e-9f05-1d2c3b4a5e6f")
_LOCAL_USER = uuid.uuid5(_NAMESPACE, "user:local")
_WORKING_STATUSES = frozenset({TalkStatus.MATCHED, TalkStatus.PROCESSED, TalkStatus.STALE})

Log = Callable[[str], None]


class ChainError(Exception):
    pass


def collect_files(path: Path, suffixes: frozenset[str]) -> list[Path]:
    candidates = [path] if path.is_file() else sorted(path.rglob("*"))
    return [
        item
        for item in candidates
        if item.is_file()
        and item.suffix.lower() in suffixes
        and not item.name.startswith((".", "~$"))
    ]


@dataclass(frozen=True)
class Ran:
    ref: ArtifactRef
    warnings: list[str]
    cached: bool
    seconds: float


def run_cached(
    stage: Stage,
    storage: Storage,
    config: BaseModel,
    artifacts: list[ArtifactRef],
    *,
    talk_id: uuid.UUID | None = None,
    conference_id: uuid.UUID | None = None,
    llm: LlmClient | None = None,
) -> Ran:
    """Результат с тем же ключом кэша берётся из хранилища без запуска этапа (ADR-005)."""
    cfg_hash = config_hash(config)
    key = artifact_key(
        stage.name,
        cache_key(stage.name, stage.version, [a.sha256 for a in artifacts], cfg_hash, talk_id),
    )
    if storage.exists(key):
        data = storage.get(key)
        return Ran(
            ArtifactRef(name=stage.name, storage_key=key, sha256=sha256_hex(data)), [], True, 0
        )
    inputs = StageInputs(
        conference_id=conference_id, talk_id=talk_id, artifacts=artifacts, config_hash=cfg_hash
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rapporteur-") as tmp:
        output = stage.run(StageContext(storage, config, Path(tmp), llm=llm), inputs)
    return Ran(output.artifact, output.warnings, False, time.monotonic() - started)


def put_blob(storage: Storage, data: bytes, name: str = SOURCE_INPUT) -> ArtifactRef:
    sha = sha256_hex(data)
    if not storage.exists(blob_key(sha)):
        storage.put(blob_key(sha), data)
    return ArtifactRef(name=name, storage_key=blob_key(sha), sha256=sha)


@dataclass
class ParsedFiles:
    slides: dict[str, tuple[str, SlidesArtifact]] = field(default_factory=dict)
    notes: dict[str, tuple[str, NotesArtifact]] = field(default_factory=dict)
    # Ссылки на артефакты разбора: входы этапа pack.
    refs: dict[str, ArtifactRef] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


def parse_folder(root: Path, storage: Storage, log: Log) -> ParsedFiles:
    """Разбор презентаций и заметок; ключ — путь от корня папки, значение — хэш и артефакт."""
    parsed = ParsedFiles()
    for suffixes, stage in (
        (PRESENTATION_SUFFIXES, SlidesStage()),
        (NOTES_SUFFIXES, NotesStage()),
    ):
        for path in collect_files(root, suffixes):
            name = path.relative_to(root).as_posix()
            source = put_blob(storage, path.read_bytes())
            try:
                ran = run_cached(stage, storage, stage.config_model(), [source])
            except StageError as error:
                parsed.failures.append(f"{name}: {error.code} — {error.message}")
                log(f"  {name}: ошибка {error.code}")
                continue
            data = storage.get(ran.ref.storage_key)
            parsed.refs[name] = ran.ref
            if stage.name == "slides":
                parsed.slides[name] = (source.sha256, SlidesArtifact.model_validate_json(data))
            else:
                parsed.notes[name] = (source.sha256, NotesArtifact.model_validate_json(data))
    log(f"Разобрано: презентаций {len(parsed.slides)}, заметок {len(parsed.notes)}")
    return parsed


def draft_registry(parsed: ParsedFiles) -> ConferenceRegistry:
    """Одна секция; доклад — группа презентаций с одинаковым названием на титульном слайде.
    Заметки привязываются к презентации с тем же именем файла без расширения."""
    groups: dict[str, list[str]] = {}
    for name, (_, slides) in parsed.slides.items():
        title = slides.title_candidates.title
        key = _normalized(title) if title else f"file:{name}"
        groups.setdefault(key, []).append(name)
    talks: list[RegistryTalk] = []
    for order, names in enumerate(groups.values(), start=1):
        first = parsed.slides[names[0]][1].title_candidates
        stems = {Path(name).with_suffix("").as_posix() for name in names}
        notes = [n for n in parsed.notes if Path(n).with_suffix("").as_posix() in stems]
        talks.append(
            RegistryTalk(
                id=f"t{order:02}",
                section="s1",
                order=order,
                title=first.title,
                speakers=first.speakers,
                affiliations=first.affiliations,
                sources=RegistrySources(slides=names, notes=notes),
                status=TalkStatus.MATCHED,
            )
        )
    return ConferenceRegistry(
        conference=RegistryConference(),
        sections=[RegistrySection(id="s1", title="Секция 1")],
        talks=talks,
    )


def load_registry(path: Path) -> ConferenceRegistry:
    try:
        return ConferenceRegistry.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ChainError(f"Реестр {path} не читается: {error}") from error


def dump_registry(registry: ConferenceRegistry) -> str:
    header = (
        "# Черновик реестра по титульным слайдам. Проверьте названия и докладчиков, заполните\n"
        "# conference.title, dates, place и сохраните как registry.yaml рядом с этим файлом.\n"
    )
    body = yaml.safe_dump(
        registry.model_dump(mode="json", exclude_none=True),
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    return header + body


@dataclass(frozen=True)
class Entities:
    conference: Conference
    sections: list[Section]
    talks: list[Talk]
    people: list[Person]
    registry_talks: dict[uuid.UUID, RegistryTalk]


def registry_entities(
    registry: ConferenceRegistry,
    *,
    title: str | None = None,
    place: str | None = None,
    dates: Sequence[dt.date] = (),
) -> Entities:
    """Сущности для сборки справки. Значения из командной строки заменяют реестр."""
    meta = registry.conference
    all_dates = list(dates) or meta.dates
    conference = Conference(
        id=uuid.uuid5(_NAMESPACE, "conference"),
        owner_id=_LOCAL_USER,
        title=title or meta.title,
        start_date=min(all_dates) if all_dates else None,
        end_date=max(all_dates) if all_dates else None,
        place=place or meta.place,
        organizer=None,
        version=1,
    )
    sections = {
        item.id: Section(
            id=uuid.uuid5(conference.id, f"section:{item.id}"),
            conference_id=conference.id,
            title=item.title,
            date=item.date,
            hall=None,
            order=order,
        )
        for order, item in enumerate(registry.sections, start=1)
    }
    people: dict[str, Person] = {}
    talks: list[Talk] = []
    registry_talks: dict[uuid.UUID, RegistryTalk] = {}
    for item in registry.talks:
        if item.section not in sections:
            raise ChainError(f"Доклад {item.id}: нет секции {item.section} в реестре")
        participants = [
            *_participants(people, item.speakers, item.affiliations, PersonRole.SPEAKER),
            *_participants(people, item.coauthors, [], PersonRole.COAUTHOR),
        ]
        talk = Talk(
            id=uuid.uuid5(conference.id, f"talk:{item.id}"),
            section_id=sections[item.section].id,
            order=item.order,
            title=item.title,
            date=item.date,
            talk_type=None,
            status=item.status,
            match_confidence=item.confidence,
            participants=participants,
            version=1,
        )
        talks.append(talk)
        registry_talks[talk.id] = item
    return Entities(
        conference, list(sections.values()), talks, list(people.values()), registry_talks
    )


def _participants(
    people: dict[str, Person], names: Sequence[str], affiliations: Sequence[str], role: PersonRole
) -> list[TalkParticipant]:
    """Организации реестра не привязаны к людям: одна организация — у всех, иначе по порядку."""
    result: list[TalkParticipant] = []
    for order, name in enumerate(names, start=1):
        if len(affiliations) == 1:
            affiliation: str | None = affiliations[0]
        elif len(affiliations) == len(names):
            affiliation = affiliations[order - 1]
        else:
            affiliation = None
        key = _normalized(name)
        person = people.setdefault(
            key,
            Person(
                id=uuid.uuid5(_NAMESPACE, f"person:{key}"), full_name=name, affiliation=affiliation
            ),
        )
        result.append(TalkParticipant(person_id=person.id, role=role, order=order))
    return result


def _normalized(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


@dataclass(frozen=True)
class LlmStages:
    facts: Stage
    summary: Stage
    facts_config: BaseModel
    summary_config: BaseModel
    client: LlmClient | None


def llm_stages(profile: ModelProfile | None, client: LlmClient | None) -> LlmStages:
    """Без профиля — заглушки: цепочка проходит целиком без модели (профиль dev)."""
    if profile is None:
        facts, summary = StubFactsStage(), StubSummaryStage()
        return LlmStages(facts, summary, facts.config_model(), summary.config_model(), None)
    return LlmStages(
        FactsStage(),
        SummaryStage(),
        FactsConfig(profile=profile),
        SummaryConfig(profile=profile),
        client,
    )


def summarize_talk(
    talk: Talk,
    item: RegistryTalk,
    conference: Conference,
    parsed: ParsedFiles,
    storage: Storage,
    stages: LlmStages,
    log: Log,
) -> Summary:
    names = [*item.sources.slides, *item.sources.notes]
    missing = [n for n in names if n not in parsed.refs]
    if missing:
        log(f"    нет разобранных файлов: {', '.join(missing)}")
    # Порядок входов — порядок реестра: презентации, затем заметки (ADR-013).
    pack = run_cached(
        PackStage(),
        storage,
        PackConfig(),
        [parsed.refs[n] for n in names if n in parsed.refs],
        talk_id=talk.id,
        conference_id=conference.id,
    )
    pack_ref = pack.ref

    facts = run_cached(
        stages.facts, storage, stages.facts_config, [pack_ref], talk_id=talk.id, llm=stages.client
    )
    verify = run_cached(
        VerifyStage(), storage, QuoteConfig(), [facts.ref, pack_ref], talk_id=talk.id
    )
    summary = run_cached(
        stages.summary,
        storage,
        stages.summary_config,
        [verify.ref, facts.ref, pack_ref],
        talk_id=talk.id,
        llm=stages.client,
    )
    claims = VerifiedClaimsArtifact.model_validate_json(storage.get(verify.ref.storage_key)).claims
    result = SummaryArtifact.model_validate_json(storage.get(summary.ref.storage_key)).summary
    confirmed = sum(claim.status is not ClaimStatus.REJECTED for claim in claims)
    log(
        f"    факты: {_timing(facts)}; цитатами подтверждено {confirmed} из {len(claims)}; "
        f"сводка {result.status.value}: {_timing(summary)}"
    )
    for warning in [*facts.warnings, *verify.warnings, *summary.warnings]:
        log(f"    ! {warning}")
    return result


def _timing(ran: Ran) -> str:
    return "из кэша" if ran.cached else f"{ran.seconds:.0f} с"


@dataclass(frozen=True)
class ReportRun:
    files: list[str]
    failures: list[str]


def run_report(
    root: Path,
    data_dir: Path,
    *,
    registry_path: Path | None,
    profile: ModelProfile | None,
    client: LlmClient | None,
    title: str | None = None,
    place: str | None = None,
    dates: Sequence[dt.date] = (),
    unreviewed_confirmed: bool = False,
    limit: int | None = None,
    template_dir: Path = STARTER_TEMPLATE,
    log: Log = print,
) -> ReportRun:
    storage = DirectoryStorage(data_dir)
    parsed = parse_folder(root, storage, log)
    failures = list(parsed.failures)

    registry_path = registry_path or data_dir / REGISTRY_FILE
    if registry_path.is_file():
        registry = load_registry(registry_path)
        log(f"Реестр: {registry_path}")
    else:
        registry = draft_registry(parsed)
        draft = data_dir / DRAFT_FILE
        draft.write_text(dump_registry(registry), encoding="utf-8")
        log(f"Реестра нет: черновик по титульным слайдам записан в {draft}")

    entities = registry_entities(registry, title=title, place=place, dates=dates)
    stages = llm_stages(profile, client)
    summaries: list[Summary] = []
    talks: list[Talk] = []
    working = [t for t in entities.talks if t.status in _WORKING_STATUSES]
    chosen = {t.id for t in working[:limit]} if limit is not None else {t.id for t in working}
    for number, talk in enumerate(entities.talks, start=1):
        if talk.id not in chosen:
            talks.append(talk)
            continue
        name = talk.title or entities.registry_talks[talk.id].id
        log(f"[{number}/{len(entities.talks)}] «{name}»")
        try:
            summary = summarize_talk(
                talk,
                entities.registry_talks[talk.id],
                entities.conference,
                parsed,
                storage,
                stages,
                log,
            )
        except StageError as error:
            failures.append(
                f"{entities.registry_talks[talk.id].id}: {error.code} — {error.message}"
            )
            log(f"    ошибка {error.code}: {error.message}")
            talks.append(talk)
            continue
        summaries.append(summary)
        talks.append(talk.model_copy(update={"status": TalkStatus.PROCESSED}))

    sources = ReportSources(
        conference=entities.conference,
        sections=entities.sections,
        talks=talks,
        people=entities.people,
        summaries=summaries,
    )
    files = _build_report(sources, storage, data_dir, template_dir, unreviewed_confirmed, log)
    return ReportRun(files=files, failures=failures)


def _build_report(
    sources: ReportSources,
    storage: Storage,
    data_dir: Path,
    template_dir: Path,
    unreviewed_confirmed: bool,
    log: Log,
) -> list[str]:
    """Справка тем же этапом report, что и в воркере (ADR-013)."""
    try:
        template = load_template(template_dir)
        selection = select_talks(sources)
    except (ReportError, TemplateError) as error:
        message = error.message if isinstance(error, TemplateError) else str(error)
        raise ChainError(f"Справка не собрана: {message}") from error
    request = ReportRequest(
        report=Report(
            id=uuid.uuid5(_NAMESPACE, "report"),
            author_id=_LOCAL_USER,
            conference_id=sources.conference.id,
            selection=selection,
            template_version=template.spec.version,
            version=1,
        ),
        conference=sources.conference,
        sections=list(sources.sections),
        talks=list(sources.talks),
        people=list(sources.people),
        summaries=list(sources.summaries),
        unreviewed_confirmed=unreviewed_confirmed,
    )
    request_ref = put_blob(storage, request.model_dump_json().encode(), name=REQUEST_INPUT)
    try:
        ran = run_cached(
            ReportStage(templates_dir=template_dir.parent),
            storage,
            ReportConfig(template=template_dir.name),
            [request_ref],
            conference_id=sources.conference.id,
        )
    except StageError as error:
        raise ChainError(f"Справка не собрана: {error.message}") from error
    for warning in ran.warnings:
        log(f"  ! {warning}")
    artifact = ReportArtifact.model_validate_json(storage.get(ran.ref.storage_key))
    return [str(data_dir / file.storage_key) for file in artifact.files]
