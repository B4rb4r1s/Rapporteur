# Проверки всего репозитория. Нужны только Docker и make; CI нет (ADR-011),
# поэтому make check запускается перед каждым слиянием в main.
DIRS := contracts pipeline backend frontend workers
.DEFAULT_GOAL := check
PROFILE ?= dev
ifeq ($(PROFILE),gpu)
GPU_COMPOSE := -f "$(CURDIR)/deploy/compose.gpu.yaml"
else ifneq ($(PROFILE),dev)
$(error PROFILE должен быть dev или gpu)
endif
COMPOSE := docker compose --env-file "$(CURDIR)/.env" -f "$(CURDIR)/deploy/compose.yaml" $(GPU_COMPOSE) --profile $(PROFILE)

.PHONY: check lint test eval up down ps logs config migrate smoke

## Явный шаг миграции очереди, выполняемый один раз перед запуском процессов
migrate:
	$(COMPOSE) up --detach --wait --wait-timeout 120 db
	$(COMPOSE) run --rm --build backend-migrate
	$(COMPOSE) run --rm --build migrate

## Dev без GPU или make up PROFILE=gpu с Ollama; ждёт healthchecks
up: migrate
	$(COMPOSE) up --build --detach --wait --wait-timeout 120

## Два задания с заглушками проходят через CPU- и GPU-воркер; только профиль dev
smoke:
	@test "$(PROFILE)" = dev || { echo "smoke разрешён только в dev" >&2; exit 2; }
	$(COMPOSE) run --rm cli smoke

## Остановка сохраняет том PostgreSQL
down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --follow

## Проверка без вывода пароля из подставленного окружения
config:
	$(COMPOSE) config --quiet

## Все проверки всех каталогов; останавливается на первом сбое
check:
	@for d in $(DIRS); do echo "== $$d"; $(MAKE) -C $$d check || exit 1; done

lint:
	@for d in $(DIRS); do echo "== $$d"; $(MAKE) -C $$d lint || exit 1; done

test:
	@for d in $(DIRS); do echo "== $$d"; $(MAKE) -C $$d test || exit 1; done

eval:
	@echo "make eval появится в P1-09 (docs/backlog.md)" >&2; exit 1
