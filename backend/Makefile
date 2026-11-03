# Все цели выполняются в контейнерах: на машине нужны только Docker и make.
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose
PY := $(COMPOSE) run --rm --build py

.PHONY: check lint test run lock

## Все проверки каталога; корневой make check вызывает эту цель
check: lint test

lint:
	$(PY) sh -c 'ruff check . && ruff format --check . && mypy'

test:
	$(PY) pytest

## Запустить web на http://127.0.0.1:8000; адрес и порт — WEB_BIND_ADDRESS и WEB_PORT
run:
	$(COMPOSE) --profile run up --build web

## Обновить uv.lock после правки pyproject.toml
lock:
	docker run --rm --user $(HOST_UID):$(HOST_GID) -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache \
	  -v $(CURDIR):/repo/backend -w /repo/backend \
	  python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 \
	  sh -c 'pip install --quiet --no-cache-dir --user uv==0.9.5 && /tmp/.local/bin/uv lock'
