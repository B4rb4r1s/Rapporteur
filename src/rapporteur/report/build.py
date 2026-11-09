"""Сборка справки по бланку: снимок входа, переменные и файлы DOCX, Markdown, PDF (spec §6.9)."""

from dataclasses import dataclass
from pathlib import Path

from rapporteur.contracts import ReportInput, ReportSelection
from rapporteur.contracts.enums import ReportFormat
from rapporteur.contracts.reports import ReportFile, ReportVariables
from rapporteur.report.assemble import (
    ReportError,
    ReportSources,
    build_report_input,
    report_variables,
    template_context,
)
from rapporteur.report.export import ExportError, export_files, render_formats
from rapporteur.report.template import TemplateError, load_template
from rapporteur.settings import TEMPLATES_DIR
from rapporteur.storage import Storage

TEMPLATE = "starter"
REPORT_FORMATS = (ReportFormat.DOCX, ReportFormat.MARKDOWN, ReportFormat.PDF)


@dataclass(frozen=True)
class BuiltReport:
    input: ReportInput
    variables: ReportVariables
    files: list[ReportFile]
    warnings: list[str]


def build_report(
    sources: ReportSources,
    selection: ReportSelection,
    unreviewed_confirmed: bool,
    storage: Storage,
    files_key: str,
    work: Path,
    *,
    template_dir: Path = TEMPLATES_DIR / TEMPLATE,
    formats: tuple[ReportFormat, ...] = REPORT_FORMATS,
) -> BuiltReport:
    """Файлы кладутся в files_key/. Ошибка выборки, бланка или подтверждения — ReportError."""
    try:
        template = load_template(template_dir)
        assembled = build_report_input(
            sources, selection, template.spec.version, unreviewed_confirmed
        )
        variables = report_variables(assembled.input)
        context = template_context(assembled.input, variables)
        rendered = render_formats(template, context, formats, work)
    except TemplateError as error:
        raise ReportError(error.message) from error
    except ExportError as error:
        raise ReportError(str(error)) from error
    files = export_files(storage, files_key, rendered)
    return BuiltReport(assembled.input, variables, files, assembled.warnings)
