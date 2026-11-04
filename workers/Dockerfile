FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-impress-nogui libreoffice-writer-nogui fonts-dejavu-core fonts-liberation \
      fonts-crosextra-carlito fonts-crosextra-caladea \
 && rm -rf /var/lib/apt/lists/*
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
RUN pip install --no-cache-dir uv==0.9.5
COPY contracts/pyproject.toml contracts/VERSION /repo/contracts/
COPY contracts/src /repo/contracts/src
COPY pipeline/pyproject.toml /repo/pipeline/
COPY pipeline/src /repo/pipeline/src
COPY pipeline/graph.yaml /repo/pipeline/graph.yaml
WORKDIR /repo/workers
COPY workers/pyproject.toml workers/uv.lock ./
COPY workers/src ./src
RUN uv sync --frozen && chmod -R a+rX /opt/venv
USER 1000:1000
