"""Сборка справки по выборке (P1-11). Конференция и доклады вымышленные."""

import copy
import datetime as dt
import io
import json
import re
import uuid
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docx import Document

from rapporteur_contracts import Conference, Person, Section, Summary, Talk
from rapporteur_contracts.entities import TalkParticipant
from rapporteur_contracts.enums import PersonRole, ReportFormat, TalkStatus
from rapporteur_pipeline.report.assemble import (
    AssembledInput,
    ReportError,
    ReportSources,
    build_report_input,
    report_variables,
    select_talks,
    template_context,
)
from rapporteur_pipeline.report.export import (
    docx_to_markdown,
    export_files,
    render_formats,
)
from rapporteur_pipeline.report.template import Period, load_template, render_docx
from rapporteur_pipeline.storage import DirectoryStorage, sha256_hex

REPO = Path(__file__).resolve().parents[2]
STARTER = REPO / "templates" / "starter"
SUMMARY_EXAMPLE = json.loads(
    (REPO / "contracts" / "examples" / "valid" / "Summary--ready.json").read_text(encoding="utf-8")
)
ROOT = uuid.UUID("00000000-0000-4000-8000-000000000001")
MODEL_TITLE = "Название, которое придумала модель"
MODEL_SPEAKER = "Докладчик Из Ответа Модели"
DAY_1, DAY_2, DAY_3 = dt.date(2026, 10, 30), dt.date(2026, 10, 31), dt.date(2026, 11, 1)


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(ROOT, name)


CONFERENCE = Conference(
    id=uid("conference"),
    owner_id=uid("local"),
    title="Данные и знания",
    start_date=DAY_1,
    end_date=DAY_3,
    place="г. Казань",
    organizer=None,
    version=1,
)
DB, STORE = uid("section:db"), uid("section:store")
SECTIONS = [
    Section(
        id=DB, conference_id=CONFERENCE.id, title="Базы данных", date=DAY_1, hall=None, order=1
    ),
    Section(
        id=STORE, conference_id=CONFERENCE.id, title="Хранилища", date=DAY_2, hall=None, order=2
    ),
]
PEOPLE = [
    Person(id=uid("ivanov"), full_name="Иванов И. И.", affiliation="Институт примера"),
    # Тот же докладчик, заведённый второй раз с лишним пробелом: считается один раз.
    Person(id=uid("ivanov-2"), full_name="Иванов  И. И.", affiliation="Институт примера"),
    Person(id=uid("kozlov"), full_name="Козлов К. К.", affiliation="Институт примера"),
    Person(id=uid("petrova"), full_name="Петрова А. А.", affiliation="Лаборатория хранения"),
    Person(id=uid("sidorov"), full_name="Сидоров П. К.", affiliation=None),
]


def talk(
    name: str,
    section: uuid.UUID,
    order: int,
    status: TalkStatus,
    speakers: list[str],
    date: dt.date | None = None,
    coauthors: tuple[str, ...] = (),
) -> Talk:
    participants = [
        TalkParticipant(person_id=uid(person), role=PersonRole.SPEAKER, order=index)
        for index, person in enumerate(speakers, start=1)
    ] + [
        TalkParticipant(person_id=uid(person), role=PersonRole.COAUTHOR, order=index)
        for index, person in enumerate(coauthors, start=1)
    ]
    return Talk(
        id=uid(f"talk:{name}"),
        section_id=section,
        order=order,
        title=name,
        date=date,
        talk_type=None,
        status=status,
        match_confidence=None,
        participants=participants,
        version=1,
    )


CACHE = talk("Кэширование запросов", DB, 1, TalkStatus.REVIEWED, ["ivanov"], coauthors=("kozlov",))
CANCELLED = talk("Отменённый доклад", DB, 2, TalkStatus.NOT_HELD, ["petrova"])
JOURNAL = talk(
    "Журналирование в колоночных хранилищах",
    STORE,
    1,
    TalkStatus.PROCESSED,
    ["petrova", "sidorov"],
    date=DAY_3,
)
NO_MATERIALS = talk("Доклад без материалов", STORE, 2, TalkStatus.INSUFFICIENT_DATA, ["sidorov"])
COMPRESSION = talk("Сжатие временных рядов", STORE, 3, TalkStatus.REVIEWED, ["ivanov-2"])
TALKS = [COMPRESSION, NO_MATERIALS, JOURNAL, CANCELLED, CACHE]


def summary(target: Talk, text: str, conclusion: str | None = None) -> Summary:
    data = copy.deepcopy(SUMMARY_EXAMPLE)
    data["id"] = str(uid(f"summary:{target.id}"))
    data["talk_id"] = str(target.id)
    data["facts"]["title"] = MODEL_TITLE
    data["facts"]["speakers"] = [MODEL_SPEAKER]
    data["sentences"] = [{"text": text, "claim_ids": ["c1"]}]
    data["conclusions"] = [{"text": conclusion, "claim_ids": ["c5"]}] if conclusion else []
    return Summary.model_validate(data)


SUMMARIES = [
    summary(
        CACHE,
        "Политика LFU снизила задержку на 23 % при 1000 запросов в секунду.",
        "LFU эффективнее LRU при неравномерной нагрузке.",
    ),
    summary(JOURNAL, "Запись в журнал занимает <1 мс & не требует блокировок [по замерам]."),
    summary(COMPRESSION, "Обзор методов сжатия телеметрии без потерь."),
]
SOURCES = ReportSources(
    conference=CONFERENCE, sections=SECTIONS, talks=TALKS, people=PEOPLE, summaries=SUMMARIES
)


def build(days: tuple[dt.date, ...] = (), sections: tuple[uuid.UUID, ...] = ()) -> AssembledInput:
    selection = select_talks(SOURCES, days, sections)
    return build_report_input(SOURCES, selection, "starter-1", unreviewed_confirmed=True)


def paragraphs(docx: bytes) -> list[str]:
    return [p.text for p in Document(io.BytesIO(docx)).paragraphs if p.text.strip()]


def squeeze(text: str) -> str:
    return "".join(text.split())


def test_talks_follow_section_and_talk_order() -> None:
    selection = select_talks(SOURCES)

    assert selection.talk_ids == [
        t.id for t in (CACHE, CANCELLED, JOURNAL, NO_MATERIALS, COMPRESSION)
    ]


def test_not_held_and_insufficient_data_stay_out_of_counts() -> None:
    assembled = build()
    variables = report_variables(assembled.input)

    assert [t.talk_id for t in assembled.input.talks] == [CACHE.id, JOURNAL.id, COMPRESSION.id]
    assert [s.status for s in assembled.input.skipped] == [
        TalkStatus.NOT_HELD,
        TalkStatus.INSUFFICIENT_DATA,
    ]
    # Иванов дважды и без соавтора Козлова: Иванов, Петрова, Сидоров.
    assert (variables.talks_count, variables.speakers_count) == (3, 3)
    assert "Не входит в справку: «Отменённый доклад» — доклад не состоялся" in assembled.warnings
    assert assembled.input.talks[2].speakers == ["Иванов И. И."]


def test_title_and_speakers_come_from_the_registry() -> None:
    assembled = build()
    first = assembled.input.talks[0]
    context = template_context(assembled.input, report_variables(assembled.input))
    text = "\n".join(paragraphs(render_docx(load_template(STARTER), context)))

    assert (first.title, first.speakers) == ("Кэширование запросов", ["Иванов И. И."])
    assert first.affiliations == ["Институт примера"]
    assert MODEL_TITLE not in text and MODEL_SPEAKER not in text
    assert "Козлов" not in text


def test_one_section_counts_only_its_talks() -> None:
    assembled = build(sections=(DB,))
    variables = report_variables(assembled.input)

    assert [t.talk_id for t in assembled.input.talks] == [CACHE.id]
    assert (variables.talks_count, variables.speakers_count) == (1, 1)
    assert (assembled.input.period_start, assembled.input.period_end) == (DAY_1, DAY_1)


def test_talk_date_overrides_section_date() -> None:
    by_talk_date = select_talks(SOURCES, days=(DAY_3,))
    by_section_date = select_talks(SOURCES, days=(DAY_2,))

    assert by_talk_date.talk_ids == [JOURNAL.id]
    assert by_section_date.talk_ids == [NO_MATERIALS.id, COMPRESSION.id]


def test_period_spans_included_talks_without_chosen_days() -> None:
    assembled = build(sections=(STORE,))

    assert (assembled.input.period_start, assembled.input.period_end) == (DAY_2, DAY_3)
    context = template_context(assembled.input, report_variables(assembled.input))
    assert context["period"] == Period(DAY_2, DAY_3)


def test_empty_selection_is_an_error() -> None:
    with pytest.raises(ReportError, match="ни один доклад"):
        select_talks(SOURCES, days=(dt.date(2026, 12, 1),))


def test_unreviewed_talks_need_confirmation() -> None:
    selection = select_talks(SOURCES)

    with pytest.raises(ReportError, match="подтвердите"):
        build_report_input(SOURCES, selection, "starter-1", unreviewed_confirmed=False)
    confirmed = build_report_input(SOURCES, selection, "starter-1", unreviewed_confirmed=True)
    assert (
        confirmed.warnings.count(
            "Сводка не проверена человеком: «Журналирование в колоночных хранилищах»"
        )
        == 1
    )


def test_reviewed_only_selection_needs_no_confirmation() -> None:
    selection = select_talks(SOURCES, section_ids=(DB,))

    assembled = build_report_input(SOURCES, selection, "starter-1", unreviewed_confirmed=False)
    assert [t.talk_id for t in assembled.input.talks] == [CACHE.id]


def test_talk_without_summary_is_skipped_with_a_warning() -> None:
    sources = ReportSources(
        conference=CONFERENCE,
        sections=SECTIONS,
        talks=TALKS,
        people=PEOPLE,
        summaries=SUMMARIES[:1],
    )
    selection = select_talks(sources, section_ids=(STORE,))

    assembled = build_report_input(sources, selection, "starter-1", unreviewed_confirmed=True)
    assert assembled.input.talks == []
    assert "Не входит в справку: «Сжатие временных рядов» — нет сводки" in assembled.warnings
    assert report_variables(assembled.input).talks_count == 0


def test_two_summaries_for_one_talk_are_rejected() -> None:
    sources = ReportSources(
        conference=CONFERENCE,
        sections=SECTIONS,
        talks=TALKS,
        people=PEOPLE,
        summaries=[SUMMARIES[0], SUMMARIES[0]],
    )

    with pytest.raises(ReportError, match="несколько сводок"):
        build_report_input(sources, select_talks(sources), "starter-1", unreviewed_confirmed=True)


def test_markdown_has_the_same_text_as_docx() -> None:
    assembled = build()
    context = template_context(assembled.input, report_variables(assembled.input))
    docx = render_docx(load_template(STARTER), context)
    markdown = docx_to_markdown(docx)

    blocks = [re.sub(r"\\(.)", r"\1", block.strip("*")) for block in markdown.split("\n\n")]
    assert [block.strip() for block in blocks] == paragraphs(docx)
    assert r"\[по замерам\]" in markdown


def test_markdown_escapes_line_start_markup() -> None:
    document = Document()
    document.add_paragraph("# не заголовок")
    document.add_paragraph("- не список")
    document.add_paragraph().add_run("Полужирный").bold = True
    buffer = io.BytesIO()
    document.save(buffer)

    markdown = docx_to_markdown(buffer.getvalue())

    assert markdown == "\\# не заголовок\n\n\\- не список\n\n**Полужирный**\n"


def test_all_formats_have_the_same_content(tmp_path: Path) -> None:
    assembled = build()
    context = template_context(assembled.input, report_variables(assembled.input))
    formats = [ReportFormat.DOCX, ReportFormat.MARKDOWN, ReportFormat.PDF]

    rendered = render_formats(load_template(STARTER), context, formats, tmp_path)

    pdf = pdfium.PdfDocument(rendered[ReportFormat.PDF])
    pdf_text = "".join(page.get_textpage().get_text_range() for page in pdf)
    docx_text = "".join(paragraphs(rendered[ReportFormat.DOCX]))
    assert squeeze(pdf_text) == squeeze(docx_text)

    storage = DirectoryStorage(tmp_path / "data")
    files = export_files(storage, "v1", rendered)
    assert [f.storage_key for f in files] == [
        "exports/v1/spravka.docx",
        "exports/v1/spravka.md",
        "exports/v1/spravka.pdf",
    ]
    for file in files:
        data = storage.get(file.storage_key)
        assert (file.sha256, file.size_bytes) == (sha256_hex(data), len(data))
