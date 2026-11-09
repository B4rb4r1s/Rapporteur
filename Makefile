# Все команды идут через Docker Compose: на машине нужны только Docker и make.
# CI нет (ADR-011): make check запускается перед каждым слиянием в main.
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose
DEV := $(COMPOSE) run --rm --build dev
WEB := $(COMPOSE) run --rm --build web-dev
PYTHON_IMAGE := python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
NODE_IMAGE := node:22-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da
# Только версии npm старше двух недель на момент блокировки; дату сдвигать вместе с make lock.
NPM_BEFORE := 2026-10-20
# Образ Ollama из compose.yaml: по digest на машине сборки, в комплекте — тегом без digest.
OLLAMA_PINNED = $(shell $(COMPOSE) config --images ollama)
OLLAMA_TAG = $(firstword $(subst @, ,$(OLLAMA_PINNED)))
BUNDLE := bundle
# Ollama другого проекта: OLLAMA_NETWORK в .env или окружении включает app-shared без своей
# Ollama (docs/ollama.md). Иначе — приложение и своя Ollama.
OLLAMA_NETWORK ?= $(shell sed -n 's/^OLLAMA_NETWORK=//p' .env 2>/dev/null)
# Хранилище приложения на хосте (RAPPORTEUR_STORAGE в .env), по умолчанию storage/.
RAPPORTEUR_STORAGE ?= $(or $(shell sed -n 's/^RAPPORTEUR_STORAGE=//p' .env 2>/dev/null),./storage)
# Все режимы: down, ps и logs видят и app, и app-shared.
ALL := $(COMPOSE) --profile shared-ollama
ifneq ($(strip $(OLLAMA_NETWORK)),)
RUN := $(ALL) up app-shared
else
RUN := $(COMPOSE) up
endif

.DEFAULT_GOAL := help
.PHONY: help up start down ps logs config check lint test format pull lock bundle install

help:
	@grep -E '^## ' Makefile | sed 's/^## //'

## make up      — собрать и запустить приложение и Ollama, http://127.0.0.1:8000
up:
	mkdir -p $(RAPPORTEUR_STORAGE)
	$(RUN) --build --detach --wait --wait-timeout 180

## make start   — запустить без сборки и скачивания образов (машина без интернета)
start:
	mkdir -p $(RAPPORTEUR_STORAGE)
	$(RUN) --detach --wait --wait-timeout 180 --no-build --pull never

## make down    — остановить; база, материалы и справки остаются в storage/
down:
	$(ALL) down

## make ps      — состояние сервисов
ps:
	$(ALL) ps

## make logs    — журнал приложения и Ollama
logs:
	$(ALL) logs --follow

## make config  — проверить compose.yaml и .env без запуска
config:
	$(COMPOSE) config --quiet

## make check   — линтеры, типы, тесты и сборка фронтенда в контейнерах
check: lint test

lint:
	$(DEV) sh -c 'ruff check . && ruff format --check . && mypy'
	$(WEB) sh -c 'eslint . && tsc -p tsconfig.json'

test:
	$(DEV) pytest
	$(WEB) sh -c 'vitest run && vite build --outDir /tmp/dist --emptyOutDir'

## make format  — поправить стиль Python
format:
	$(DEV) sh -c 'ruff check --fix . && ruff format .'

## make pull MODEL=qwen3.5:9b — скачать модель в OLLAMA_MODELS_DIR (нужен интернет)
pull:
	@test -n "$(MODEL)" || { echo "укажите MODEL" >&2; exit 2; }
	$(COMPOSE) run --rm pull $(MODEL)

## make lock    — обновить uv.lock и package-lock.json после правки зависимостей
lock:
	docker run --rm --user $(HOST_UID):$(HOST_GID) -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache \
	  -v $(CURDIR):/app -w /app $(PYTHON_IMAGE) \
	  sh -c 'pip install --quiet --no-cache-dir --user uv==0.9.5 && /tmp/.local/bin/uv lock'
	docker run --rm --user $(HOST_UID):$(HOST_GID) -e HOME=/tmp \
	  -v $(CURDIR)/frontend:/app -w /app $(NODE_IMAGE) \
	  npm install --package-lock-only --ignore-scripts --no-audit --no-fund --before=$(NPM_BEFORE)

## make bundle [MODEL="qwen3.5:9b …"] — комплект для машины без интернета в bundle/
## Без MODEL берётся модель профиля по умолчанию из profiles.yaml (docs/offline.md).
bundle:
	$(COMPOSE) build app
	docker pull $(OLLAMA_PINNED)
	docker tag $(OLLAMA_PINNED) $(OLLAMA_TAG)
	mkdir -p $(BUNDLE)/models
	$(COMPOSE) run --rm export-model $(MODEL)
	docker save --output $(BUNDLE)/images.tar rapporteur:local $(OLLAMA_TAG)
	cp compose.yaml Makefile $(BUNDLE)/
	cp docs/offline.md $(BUNDLE)/README.md
	sed 's|^OLLAMA_MODELS_DIR=.*|OLLAMA_MODELS_DIR=./models|' .env.example > $(BUNDLE)/.env.example
	printf '\n# Образ Ollama из комплекта: тег вместо digest (docs/offline.md).\nOLLAMA_IMAGE=%s\n' \
	  "$(OLLAMA_TAG)" >> $(BUNDLE)/.env.example
	cd $(BUNDLE) && find images.tar models -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
	@du -sh $(BUNDLE)

## make install — в каталоге комплекта: проверить контрольные суммы и загрузить образы
install:
	sha256sum --check --quiet SHA256SUMS
	docker load --input images.tar
	@test -f .env || { cp .env.example .env; echo "Создан .env: проверьте WEB_BIND_ADDRESS и WEB_PORT"; }
