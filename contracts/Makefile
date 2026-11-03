# Все цели выполняются в контейнерах: на машине нужны только Docker и make.
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose
PY := $(COMPOSE) run --rm --build py
TS := $(COMPOSE) run --rm ts

.PHONY: all check generate test lint check-generated lock

all: generate lint test

## Все проверки каталога; корневой make check вызывает эту цель
check: lint test check-generated

## JSON Schema и TS-типы одной командой
generate:
	$(PY) python -m rapporteur_contracts.export
	$(TS) sh -c 'npm ci --ignore-scripts --no-audit --no-fund && node scripts/schema-to-ts.mjs'

test:
	$(PY) pytest

lint:
	$(PY) sh -c 'ruff check . && ruff format --check . && mypy'
	$(TS) sh -c 'npm ci --ignore-scripts --no-audit --no-fund && npx tsc -p tsconfig.json'

## Падает, если сгенерированные файлы отстали от моделей
check-generated: generate
	git diff --exit-code -- schema ts

## Обновить uv.lock после правки pyproject.toml
lock:
	docker run --rm --user $(HOST_UID):$(HOST_GID) -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache \
	  -v $(CURDIR):/repo/contracts -w /repo/contracts \
	  python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 \
	  sh -c 'pip install --quiet --no-cache-dir --user uv==0.9.5 && /tmp/.local/bin/uv lock'
