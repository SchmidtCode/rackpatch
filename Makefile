SHELL := /bin/bash
DC := docker compose
EXEC := $(DC) exec -T api

.PHONY: up build logs shell worker-logs rollback validate release-check check-updates check-packages agent-install

up:
	$(DC) up -d --build --remove-orphans

build:
	$(DC) build

logs:
	$(DC) logs -f api

shell:
	$(DC) exec api bash

worker-logs:
	$(DC) logs -f worker

rollback:
	$(EXEC) python3 scripts/rollback_stack.py --stack $(STACK)

validate:
	./scripts/validate-policy.py
	$(EXEC) python3 scripts/check_stack_updates.py --window all >/dev/null
	$(EXEC) python3 scripts/check_package_updates.py --scope all >/dev/null

release-check:
	python3 scripts/release_check.py

check-updates:
	$(EXEC) python3 scripts/check_stack_updates.py --window $(or $(WINDOW),all) $(if $(STACKS),--stack $(STACKS),)

check-packages:
	$(EXEC) python3 scripts/check_package_updates.py --scope $(or $(SCOPE),all) $(if $(HOSTS),--host $(HOSTS),)

agent-install:
	./scripts/install-agent.sh --server-url $(SERVER_URL) --bootstrap-token $(BOOTSTRAP_TOKEN) --mode $(or $(MODE),container) $(if $(INSTALL_SOURCE),--install-source $(INSTALL_SOURCE),) $(if $(INSTALL_REF),--install-ref $(INSTALL_REF),)
