FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir uv==0.9.5

COPY contracts/pyproject.toml contracts/VERSION /repo/contracts/
COPY contracts/src /repo/contracts/src
COPY pipeline/pyproject.toml /repo/pipeline/
COPY pipeline/src /repo/pipeline/src
WORKDIR /repo/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
RUN uv sync --frozen && chmod -R a+rwX /opt/venv
