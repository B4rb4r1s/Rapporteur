"""Рендер слайдов в PNG: pptx через LibreOffice в PDF, PDF через PDFium (spec §6.2).

LibreOffice запускается с отдельным профилем, отключёнными макросами и лимитом времени;
сети в контейнере нет (spec §10).
"""

import io
import json
import resource
import subprocess
from pathlib import Path

import pypdfium2 as pdfium

from rapporteur_pipeline.ingest.limits import PresentationConfig
from rapporteur_pipeline.stage import FailureCode, StageError

# Скрытые слайды тоже выгружаются, чтобы страница PDF совпадала с номером слайда.
_PDF_FILTER = "pdf:impress_pdf_Export:" + json.dumps(
    {"ExportHiddenSlides": {"type": "boolean", "value": "true"}}
)

_PROFILE_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
  </item>
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  </item>
</oor:items>
"""


def pptx_to_pdf(pptx: Path, work_dir: Path, limits: PresentationConfig) -> Path:
    profile = work_dir / "lo-profile"
    (profile / "user").mkdir(parents=True, exist_ok=True)
    (profile / "user" / "registrymodifications.xcu").write_text(_PROFILE_SETTINGS)
    out_dir = work_dir / "pdf"
    command = [
        "soffice",
        f"-env:UserInstallation={profile.as_uri()}",
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        "--convert-to",
        _PDF_FILTER,
        "--outdir",
        str(out_dir),
        str(pptx),
    ]
    timeout = limits.render_timeout_s

    def cpu_limit() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout))

    try:
        subprocess.run(
            command, capture_output=True, timeout=timeout, check=False, preexec_fn=cpu_limit
        )
    except subprocess.TimeoutExpired as error:
        raise StageError(
            FailureCode.RENDER_FAILED, f"LibreOffice не уложился в {timeout} с"
        ) from error
    result = out_dir / f"{pptx.stem}.pdf"
    if not result.is_file():
        raise StageError(FailureCode.RENDER_FAILED, "LibreOffice не смог отрисовать слайды")
    return result


def render_page(pdf: pdfium.PdfDocument, page_no: int, limits: PresentationConfig) -> bytes:
    page = pdf[page_no - 1]
    width, height = page.get_size()
    scale = limits.render_long_side_px / max(width, height, 1.0)
    if width * scale * height * scale > limits.max_render_pixels:
        scale = (limits.max_render_pixels / (width * height)) ** 0.5
    image = page.render(scale=scale).to_pil()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
