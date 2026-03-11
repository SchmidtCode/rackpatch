SHELL := /bin/bash
DC := docker compose
EXEC := $(DC) exec -T ops-api

.PHONY: up build logs shell worker-logs backup-legacy rollback validate check-updates check-packages agent-install

up:
	$(DC) up -d --build

build:
	$(DC) build

logs:
	$(DC) logs -f ops-api

shell:
	$(DC) exec ops-api bash

worker-logs:
	$(DC) logs -f ops-worker

backup-legacy:
	./scripts/backup_legacy_stack.sh

rollback:
	$(EXEC) python3 scripts/rollback_stack.py --stack $(STACK)

validate:
	./scripts/validate-policy.py
	$(EXEC) python3 scripts/check_stack_updates.py --window all >/dev/null
	$(EXEC) python3 scripts/check_package_updates.py --scope all >/dev/null

check-updates:
	$(EXEC) python3 scripts/check_stack_updates.py --window $(or $(WINDOW),all) $(if $(STACKS),--stack $(STACKS),)

check-packages:
	$(EXEC) python3 scripts/check_package_updates.py --scope $(or $(SCOPE),all) $(if $(HOSTS),--host $(HOSTS),)

agent-install:
	./scripts/install-agent.sh --server-url $(SERVER_URL) --bootstrap-token $(BOOTSTRAP_TOKEN) --mode $(or $(MODE),container)
