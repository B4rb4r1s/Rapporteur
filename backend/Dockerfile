FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir uv==0.9.5

WORKDIR /repo/backend
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen && chmod -R a+rwX /opt/venv
