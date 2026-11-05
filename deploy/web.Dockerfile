# Контекст сборки — корень репозитория. В работающем web Node не нужен (ADR-001).
FROM node:22-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/vite build

FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    RAPPORTEUR_STATIC_DIR=/opt/rapporteur/static
RUN pip install --no-cache-dir uv==0.9.5
COPY contracts/pyproject.toml contracts/VERSION /repo/contracts/
COPY contracts/src /repo/contracts/src
COPY pipeline/pyproject.toml /repo/pipeline/
COPY pipeline/src /repo/pipeline/src
COPY pipeline/graph.yaml /repo/pipeline/graph.yaml
COPY workers/pyproject.toml /repo/workers/
COPY workers/src /repo/workers/src
WORKDIR /repo/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
RUN uv sync --frozen --no-dev && chmod -R a+rX /opt/venv
COPY --from=frontend /build/dist /opt/rapporteur/static
USER 1000:1000
CMD ["uvicorn", "rapporteur_backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
