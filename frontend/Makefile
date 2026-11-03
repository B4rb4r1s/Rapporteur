# Все цели выполняются в контейнерах: на машине нужны только Docker и make.
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose
TS := $(COMPOSE) run --rm --build ts

# Только версии старше двух недель на момент блокировки; дату сдвигать вместе с make lock.
NPM_BEFORE := 2026-10-20

.PHONY: check lint test build lock

## Все проверки каталога; корневой make check вызывает эту цель
check: lint test build

lint:
	$(TS) sh -c 'eslint . && tsc -p tsconfig.json'

test:
	$(TS) vitest run

## Сборка в dist/: проверяет, что бандл собирается без внешних ресурсов
build:
	$(TS) vite build

## Обновить package-lock.json после правки package.json
lock:
	docker run --rm --user $(HOST_UID):$(HOST_GID) -e HOME=/tmp \
	  -v $(CURDIR):/repo/frontend -w /repo/frontend \
	  node:22-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da \
	  npm install --package-lock-only --ignore-scripts --no-audit --no-fund --before=$(NPM_BEFORE)
