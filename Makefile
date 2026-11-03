# Проверки всего репозитория. Нужны только Docker и make; CI нет (ADR-011),
# поэтому make check запускается перед каждым слиянием в main.
DIRS := contracts backend frontend

.PHONY: check lint test eval

## Все проверки всех каталогов; останавливается на первом сбое
check:
	@for d in $(DIRS); do echo "== $$d"; $(MAKE) -C $$d check || exit 1; done

lint:
	@for d in $(DIRS); do echo "== $$d"; $(MAKE) -C $$d lint || exit 1; done

test:
	@for d in $(DIRS); do echo "== $$d"; $(MAKE) -C $$d test || exit 1; done

eval:
	@echo "make eval появится в P1-09 (docs/backlog.md)" >&2; exit 1
