SHELL := /bin/bash
DC := docker compose
DEV_DC := docker compose -f docker-compose.yml -f docker-compose.dev.yml
EXEC := $(DC) exec -T api

.PHONY: up dev-up build dev-build logs shell worker-logs rollback validate release-check check-updates check-packages agent-install

up:
	$(DC) pull
	$(DC) up -d --remove-orphans

dev-up:
	$(DEV_DC) up -d --build --remove-orphans

build:
	$(DC) pull

dev-build:
	$(DEV_DC) build

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
	python3 -m compileall app scripts/host-maintenance >/dev/null
	bash -n scripts/install-agent.sh scripts/update-agent.sh scripts/enable-agent-host-maintenance.sh

release-check:
	python3 scripts/release_check.py

check-updates:
	@echo "check-updates is deprecated: remote stack discovery is agent-reported now."
	@echo "Use the UI Stacks page or queue docker_update against enrolled agents."
	@exit 1

check-packages:
	@echo "check-packages is deprecated: package maintenance is agent/helper-backed now."
	@echo "Use the web UI, Telegram, or POST /api/v1/jobs with kind=package_check against helper-enabled agents."
	@exit 1

agent-install:
	./scripts/install-agent.sh --server-url $(SERVER_URL) --bootstrap-token $(BOOTSTRAP_TOKEN) --mode $(or $(MODE),container) $(if $(INSTALL_SOURCE),--install-source $(INSTALL_SOURCE),) $(if $(INSTALL_REF),--install-ref $(INSTALL_REF),)
