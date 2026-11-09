# Один образ приложения (ADR-014). React собирается на этапе Node; в работающем приложении
# Node не нужен (ADR-001). Этапы web-deps и dev нужны только для проверок: make check.

FROM node:22-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS web-deps
# Зависимости лежат на уровень выше исходников: в make check исходники подключаются
# в /app/frontend, а Node находит пакеты в /app/node_modules, поднимаясь по каталогам.
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund && chmod -R a+rX /app/node_modules
ENV PATH=/app/node_modules/.bin:$PATH
WORKDIR /app/frontend

FROM web-deps AS web
COPY frontend/ ./
RUN tsc -p tsconfig.json --noEmit && vite build

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS base
# LibreOffice рендерит pptx в PDF и справку DOCX в PDF; Carlito и Caladea метрически
# совместимы с Calibri и Cambria. ffprobe читает длительность записей.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg libreoffice-impress-nogui libreoffice-writer-nogui fonts-dejavu-core \
      fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
 && rm -rf /var/lib/apt/lists/*
# Во время работы ничего не скачивается из интернета (spec §2).
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
RUN pip install --no-cache-dir uv==0.9.5
WORKDIR /app
COPY pyproject.toml uv.lock ./

# Проверки: исходники подключаются с хоста поверх /app, пакет установлен в режиме
# редактирования и читается из /app/src.
FROM base AS dev
COPY src ./src
RUN uv sync --frozen && chmod -R a+rwX /opt/venv

FROM base AS app
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
# Пакет ставится в режиме редактирования: settings.ROOT указывает на /app, где лежат
# prompts/, templates/ и profiles.yaml.
RUN uv sync --frozen --no-dev && chmod -R a+rX /opt/venv
COPY prompts ./prompts
COPY templates ./templates
COPY profiles.yaml ./
COPY --from=web /app/frontend/dist ./static
ENV RAPPORTEUR_STATIC_DIR=/app/static \
    RAPPORTEUR_DATA_DIR=/data
# Новый именованный том получает владельца от этого каталога.
RUN mkdir /data && chown 1000:1000 /data
USER 1000:1000
EXPOSE 8000
CMD ["uvicorn", "rapporteur.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
