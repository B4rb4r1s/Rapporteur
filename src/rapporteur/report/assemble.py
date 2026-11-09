"""Сборка справки по выборке докладов (spec §6.9, ADR-006, ADR-008).

Название, докладчики и организации берутся из реестра, текст — из сводки. Ответ модели
(FactsDraft.title, speakers) в справку не попадает: это только подсказка для сверки с реестром.
Счётчики считает код по докладам, которые вошли в справку; «не состоялся» и «Недостаточно
информации» в них не входят.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from rapporteur.contracts import Conference, ReportInput, Section, Summary, Talk
from rapporteur.contracts.enums import (
    REPORTABLE_TALK_STATUSES,
    PersonRole,
    SummaryStatus,
    TalkStatus,
)
from rapporteur.contracts.reports import (
    ReportSelection,
    ReportTalk,
    ReportVariables,
    SkippedTalk,
)
from rapporteur.report.template import Period

_SKIP_REASONS = {
    TalkStatus.NOT_HELD: "доклад не состоялся",
    TalkStatus.INSUFFICIENT_DATA: "недостаточно информации",
    TalkStatus.DRAFT: "материалы не привязаны",
    TalkStatus.MATCHED: "сводка ещё не готова",
}


class ReportError(Exception):
    pass


@dataclass(frozen=True)
class ReportSources:
    """Данные конференции для справки; summaries — текущие сводки докладов."""

    conference: Conference
    sections: Sequence[Section]
    talks: Sequence[Talk]
    summaries: Sequence[Summary]

    @cached_property
    def section_by_id(self) -> dict[UUID, Section]:
        return {section.id: section for section in self.sections}

    @cached_property
    def talk_by_id(self) -> dict[UUID, Talk]:
        return {talk.id: talk for talk in self.talks}

    @cached_property
    def summary_by_talk(self) -> dict[UUID, Summary]:
        result: dict[UUID, Summary] = {}
        for summary in self.summaries:
            if summary.talk_id in result:
                raise ReportError(f"У доклада {summary.talk_id} передано несколько сводок")
            result[summary.talk_id] = summary
        return result

    def talk_day(self, talk: Talk) -> dt.date | None:
        """День доклада — его дата, а если её нет, дата секции (spec §6.9)."""
        return talk.date or self.section_by_id[talk.section_id].date

    def ordered_talks(self) -> list[Talk]:
        sections = self.section_by_id
        return sorted(self.talks, key=lambda talk: (sections[talk.section_id].order, talk.order))


def select_talks(
    sources: ReportSources, days: Sequence[dt.date] = (), section_ids: Sequence[UUID] = ()
) -> ReportSelection:
    """Пустой список дней или секций не ограничивает выборку. Доклад без даты не попадает
    в выборку по дням: его день неизвестен."""
    chosen = [
        talk.id
        for talk in sources.ordered_talks()
        if (not section_ids or talk.section_id in section_ids)
        and (not days or sources.talk_day(talk) in days)
    ]
    if not chosen:
        raise ReportError("В выбранные дни и секции не попал ни один доклад")
    return ReportSelection(days=list(days), section_ids=list(section_ids), talk_ids=chosen)


@dataclass(frozen=True)
class AssembledInput:
    input: ReportInput
    warnings: list[str]


def build_report_input(
    sources: ReportSources,
    selection: ReportSelection,
    template_version: str,
    unreviewed_confirmed: bool,
) -> AssembledInput:
    """Снимок входа сборки. Без подтверждения непроверенные доклады — ошибка (ADR-006)."""
    included: list[ReportTalk] = []
    skipped: list[SkippedTalk] = []
    warnings: list[str] = []

    for talk_id in selection.talk_ids:
        talk = sources.talk_by_id.get(talk_id)
        if talk is None:
            raise ReportError(f"Доклада {talk_id} нет в реестре конференции")
        summary = sources.summary_by_talk.get(talk_id)
        if talk.status not in REPORTABLE_TALK_STATUSES:
            reason = _SKIP_REASONS.get(talk.status, f"статус {talk.status.value}")
        elif summary is None:
            reason = "нет сводки"
        elif not summary.sentences:
            reason = "сводка пуста: нужна правка человеком"
        elif talk.title is None:
            reason = "у доклада нет названия в реестре"
        else:
            included.append(_report_talk(sources, talk, talk.title, summary))
            if summary.status is SummaryStatus.NEEDS_ATTENTION:
                warnings.append(f"Сводка не прошла проверки, нужна правка: {_name(talk)}")
            if talk.status is TalkStatus.STALE:
                warnings.append(f"Сводка устарела после правки источника: {_name(talk)}")
            elif talk.status is not TalkStatus.REVIEWED:
                warnings.append(f"Сводка не проверена человеком: {_name(talk)}")
            continue
        skipped.append(SkippedTalk(talk_id=talk_id, title=talk.title, status=talk.status))
        warnings.append(f"Не входит в справку: {_name(talk)} — {reason}")

    start, end = _period(sources, selection, included)
    try:
        report_input = ReportInput(
            conference_title=sources.conference.title,
            period_start=start,
            period_end=end,
            place=sources.conference.place,
            organizer=sources.conference.organizer,
            selection=selection,
            talks=included,
            skipped=skipped,
            template_version=template_version,
            unreviewed_confirmed=unreviewed_confirmed,
        )
    except ValidationError as error:
        if any(item["type"] == "unreviewed_not_confirmed" for item in error.errors()):
            raise ReportError(
                "В справке есть непроверенные доклады: подтвердите сборку без проверки"
            ) from error
        raise
    return AssembledInput(input=report_input, warnings=warnings)


def _report_talk(sources: ReportSources, talk: Talk, title: str, summary: Summary) -> ReportTalk:
    speakers = [person for person in talk.speakers if person.role is PersonRole.SPEAKER]
    return ReportTalk(
        talk_id=talk.id,
        section_title=sources.section_by_id[talk.section_id].title,
        title=title,
        speakers=[_clean(person.name) for person in speakers],
        affiliations=_unique(
            _clean(person.affiliation) for person in speakers if person.affiliation
        ),
        status=talk.status,
        summary=" ".join(line.text for line in summary.sentences),
        conclusions=[line.text for line in summary.conclusions],
    )


def _period(
    sources: ReportSources, selection: ReportSelection, included: Sequence[ReportTalk]
) -> tuple[dt.date | None, dt.date | None]:
    """Выбранные дни, иначе дни вошедших докладов, иначе даты конференции."""
    if selection.days:
        return min(selection.days), max(selection.days)
    days = [
        day
        for item in included
        if (day := sources.talk_day(sources.talk_by_id[item.talk_id])) is not None
    ]
    if days:
        return min(days), max(days)
    return sources.conference.start_date, sources.conference.end_date


def _name(talk: Talk) -> str:
    return f"«{talk.title}»" if talk.title else str(talk.id)


def _clean(value: str) -> str:
    return " ".join(value.split())


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def report_variables(report_input: ReportInput) -> ReportVariables:
    """Докладчики считаются по уникальным ФИО, без соавторов (spec §6.9)."""
    speakers = {
        " ".join(name.casefold().replace("ё", "е").split())
        for talk in report_input.talks
        for name in talk.speakers
    }
    return ReportVariables(talks_count=len(report_input.talks), speakers_count=len(speakers))


def template_context(report_input: ReportInput, variables: ReportVariables) -> dict[str, Any]:
    """Контекст бланка: имена совпадают с templates/starter/variables.yaml."""
    period = None
    if report_input.period_start is not None:
        period = Period(
            report_input.period_start, report_input.period_end or report_input.period_start
        )
    return {
        "title": report_input.conference_title,
        "period": period,
        "place": report_input.place,
        "organizer": report_input.organizer,
        "talks_count": variables.talks_count,
        "speakers_count": variables.speakers_count,
        "talks": list(report_input.talks),
    }
