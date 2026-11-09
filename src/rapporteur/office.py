"""Конвертация в PDF через LibreOffice headless: слайды pptx (spec §6.2) и справка DOCX (§6.9).

Каждый запуск идёт с отдельным профилем, отключёнными макросами и лимитом процессорного
времени; сети в контейнере нет (spec §10).
"""

import resource
import subprocess
from pathlib import Path

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


class ConversionError(Exception):
    pass


def convert_to_pdf(source: Path, work_dir: Path, export_filter: str, timeout_s: int) -> Path:
    """export_filter — цель для --convert-to, например «pdf:writer_pdf_Export»."""
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
        export_filter,
        "--outdir",
        str(out_dir),
        str(source),
    ]

    def cpu_limit() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_s, timeout_s))

    try:
        subprocess.run(
            command, capture_output=True, timeout=timeout_s, check=False, preexec_fn=cpu_limit
        )
    except subprocess.TimeoutExpired as error:
        raise ConversionError(f"LibreOffice не уложился в {timeout_s} с") from error
    result = out_dir / f"{source.stem}.pdf"
    if not result.is_file():
        raise ConversionError("LibreOffice не смог сконвертировать документ в PDF")
    return result
