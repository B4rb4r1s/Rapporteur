# Все цели выполняются в контейнерах: на машине нужны только Docker и make.
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose
PY := $(COMPOSE) run --rm --build py

.PHONY: check lint test run report graph lock

## Все проверки каталога; корневой make check вызывает эту цель
check: lint test

lint:
	$(PY) sh -c 'ruff check . && ruff format --check . && mypy'

test:
	$(PY) pytest

## Прогнать этап на папке: make run STAGE=slides INPUT=/путь/к/конференции
## Результаты пишутся в .rapporteur-data/ (или в RAPPORTEUR_DATA), каталог в git не входит.
run:
	@test -n "$(STAGE)" -a -n "$(INPUT)" || { echo "укажите STAGE и INPUT" >&2; exit 2; }
	mkdir -p .rapporteur-data
	RAPPORTEUR_INPUT="$(abspath $(INPUT))" $(PY) rapporteur run $(STAGE) /input --data-dir /data

## Справка по папке через локальную модель: make report INPUT=/путь [PROFILE=local-fast] [ARGS=...]
## Сначала поднять Ollama: OLLAMA_MODELS_DIR=... docker compose -f ../deploy/ollama/compose.yaml up -d
## Реестр — .rapporteur-data/registry.yaml; без него пишется черновик registry-draft.yaml.
## Без редактора все сводки непроверенные, поэтому цель подтверждает сборку (--unreviewed).
## ARGS: --title, --dates 2026-04-20,2026-04-21, --place, --talks N, --stub.
report:
	@test -n "$(INPUT)" || { echo "укажите INPUT" >&2; exit 2; }
	mkdir -p .rapporteur-data
	RAPPORTEUR_INPUT="$(abspath $(INPUT))" $(COMPOSE) run --rm --build llm \
	  rapporteur report /input --data-dir /data --unreviewed $(if $(PROFILE),--profile $(PROFILE)) $(ARGS)

## Проверить граф этапов pipeline/graph.yaml и показать порядок исполнения
graph:
	$(PY) rapporteur graph

## Обновить uv.lock после правки pyproject.toml
lock:
	docker run --rm --user $(HOST_UID):$(HOST_GID) -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache \
	  -v $(abspath ..):/repo -w /repo/pipeline \
	  python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 \
	  sh -c 'pip install --quiet --no-cache-dir --user uv==0.9.5 && /tmp/.local/bin/uv lock'
