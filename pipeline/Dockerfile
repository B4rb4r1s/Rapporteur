# Контекст сборки — корень репозитория: pipeline зависит от contracts (ADR-007).
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

# LibreOffice рендерит pptx в PDF; шрифты Carlito и Caladea метрически совместимы с Calibri и Cambria.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-impress-nogui fonts-dejavu-core fonts-liberation \
      fonts-crosextra-carlito fonts-crosextra-caladea \
 && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir uv==0.9.5

COPY contracts/pyproject.toml contracts/VERSION /repo/contracts/
COPY contracts/src /repo/contracts/src
WORKDIR /repo/pipeline
COPY pipeline/pyproject.toml pipeline/uv.lock ./
COPY pipeline/src ./src
RUN uv sync --frozen && chmod -R a+rwX /opt/venv
