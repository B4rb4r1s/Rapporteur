"""Этап report: справка по запросу сборки и сводкам выбранных докладов (spec §6.9, ADR-013).

Этап не входит в граф: задачу ставит backend, когда пользователь собирает справку. Вход —
ReportRequest (name report_request) с текущими сводками, в том числе исправленными человеком.
Выход — ReportArtifact со снимком ReportInput и файлами DOCX, Markdown и PDF.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from rapporteur_contracts import (
    ReportArtifact,
    ReportRequest,
    StageInputs,
    StageOutput,
)
from rapporteur_contracts.enums import ReportFormat
from rapporteur_pipeline.report.assemble import (
    ReportError,
    ReportSources,
    build_report_input,
    report_variables,
    template_context,
)
from rapporteur_pipeline.report.export import ExportError, export_files, render_formats
from rapporteur_pipeline.report.template import TemplateError, load_template
from rapporteur_pipeline.stage import (
    FailureCode,
    StageContext,
    StageError,
    make_meta,
    publish,
    read_input,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "templates"
REQUEST_INPUT = "report_request"
REPORT_FORMATS = (ReportFormat.DOCX, ReportFormat.MARKDOWN, ReportFormat.PDF)


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Каталог бланка в templates/; его версия должна совпасть с версией в справке.
    template: str = Field(default="starter", pattern=r"^[a-z0-9_-]+$")
    formats: tuple[ReportFormat, ...] = REPORT_FORMATS


class ReportStage:
    name: ClassVar[str] = "report"
    version: ClassVar[str] = "1.0.0"
    config_model: ClassVar[type[BaseModel]] = ReportConfig

    def __init__(self, templates_dir: Path = TEMPLATES_DIR) -> None:
        self.templates_dir = templates_dir

    def run(self, ctx: StageContext, inputs: StageInputs) -> StageOutput:
        config = ctx.config
        assert isinstance(config, ReportConfig)
        unknown = {ref.name for ref in inputs.artifacts} - {REQUEST_INPUT}
        if unknown:
            raise ValueError(f"этап report не принимает входы {', '.join(sorted(unknown))}")
        request = ReportRequest.model_validate_json(read_input(ctx, inputs, REQUEST_INPUT))
        sources = ReportSources(
            conference=request.conference,
            sections=request.sections,
            talks=request.talks,
            people=request.people,
            summaries=request.summaries,
        )
        meta = make_meta(self, inputs, stub=False)
        try:
            template = load_template(self.templates_dir / config.template)
            if template.spec.version != request.report.template_version:
                raise ReportError(
                    f"Справка рассчитана на бланк {request.report.template_version}, "
                    f"а в {config.template} лежит {template.spec.version}"
                )
            assembled = build_report_input(
                sources,
                request.report.selection,
                template.spec.version,
                request.unreviewed_confirmed,
            )
            variables = report_variables(assembled.input)
            context = template_context(assembled.input, variables)
            rendered = render_formats(template, context, config.formats, ctx.tmp_dir)
        except (ReportError, TemplateError) as error:
            message = error.message if isinstance(error, TemplateError) else str(error)
            raise StageError(FailureCode.REPORT_INVALID, message) from error
        except ExportError as error:
            raise StageError(FailureCode.RENDER_FAILED, str(error)) from error
        # Каталог версии — по ключу кэша: тот же запрос даёт те же файлы (ADR-005).
        files = export_files(ctx.storage, meta.cache_key[:16], rendered)
        artifact = ReportArtifact(
            meta=meta,
            report_id=request.report.id,
            input=assembled.input,
            variables=variables,
            files=files,
        )
        return publish(ctx, meta, artifact, assembled.warnings)
