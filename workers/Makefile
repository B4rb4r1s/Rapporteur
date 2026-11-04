export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)
COMPOSE := docker compose
PY := $(COMPOSE) run --rm --build --no-deps py

.PHONY: check lint test lock
check: lint test

lint:
	$(PY) sh -c 'ruff check . && ruff format --check . && mypy'

test:
	@$(COMPOSE) up --detach --wait db && $(COMPOSE) run --rm --build py pytest; \
	  result=$$?; $(COMPOSE) down; exit $$result

lock:
	docker run --rm --user $(HOST_UID):$(HOST_GID) -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv-cache \
	  -v $(abspath ..):/repo -w /repo/workers \
	  python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 \
	  sh -c 'pip install --quiet --no-cache-dir --user uv==0.9.5 && /tmp/.local/bin/uv lock'
