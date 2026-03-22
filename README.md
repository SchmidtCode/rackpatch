# rackpatch

rackpatch is a compose-first homelab maintenance appliance for Docker stacks, helper-gated Debian or Ubuntu package maintenance, and opt-in Proxmox node actions.

Version in this repo: `v0.3.7`

## What rackpatch does

- Tracks Docker stacks from your site catalog and discovered compose projects.
- Uses enrolled agents to discover compose projects and apply Docker stack updates.
- Handles guest package checks and guest patching through the limited host-maintenance helper.
- Can expose helper-gated Proxmox node patch and reboot actions when you explicitly enable them on those nodes.
- Provides a web UI, Telegram control surface, generated install/update commands, and machine-readable API context.
- Surfaces release status for the control plane and enrolled agents when the public repo points at GitHub.

## Highlights

- Page-based UI with focused `Overview`, `Stacks`, `Hosts`, `Agents`, `Jobs`, `Approvals`, `Schedules`, `Backups`, and `Settings` views.
- Published GHCR images are the default deployment path for the control plane and containerized agents.
- `/api/v1/context` and `/api/v1/job-kinds` expose machine-readable setup and job metadata.
- Agent-driven Docker updates support lightweight pre-update stack backups with retention controls.

## Runtime services

- `web`: rackpatch web UI on `RACKPATCH_HTTP_PORT` (default `3011`)
- `api`: backend API
- `worker`: database-backed job runner
- `db`: Postgres
- `telegram`: Telegram bot for remote job control
- `notify`: optional send-only Telegram or webhook notifier
- `rackpatch-agent`: remote polling agent for Debian, Ubuntu, and Docker hosts

## Quick start

```bash
cd /srv/compose/rackpatch
cp .env.example .env
docker compose pull
docker compose up -d --remove-orphans
```

The UI is available at `http://<host>:3011`.

Default bootstrap login:

- Username: value of `RACKPATCH_ADMIN_USERNAME` (default `admin`)
- Password: value of `RACKPATCH_ADMIN_PASSWORD`

If `RACKPATCH_AGENT_BOOTSTRAP_TOKEN=bootstrap-me`, rackpatch generates a stable bootstrap token on first start and exposes it in `Settings`.

## Docs

- [Docs index](docs/README.md)
- [Setup and configuration](docs/setup-and-configuration.md)
- [Agents and host maintenance](docs/agents-and-host-maintenance.md)
- [Automation and control surfaces](docs/automation-and-control-surfaces.md)
- [Release and publishing](docs/release-and-publishing.md)

## Common commands

- `make up`: pull published images and start the stack
- `make dev-up`: run the stack with `docker-compose.dev.yml` source-build overrides
- `make logs`: tail API logs
- `make worker-logs`: tail worker logs
- `make validate`: run validation checks
- `make release-check`: verify the repo is safe to publish

If you change `.env`, rerun `docker compose up -d --remove-orphans` so containers are recreated with the new environment.

## Repository layout

```text
app/         Python services
config/      tracked stack catalog defaults
docs/        long-form setup, operations, and release guides
playbooks/   Ansible playbooks
roles/       Ansible roles
scripts/     install, update, validation, and operator utilities
sites/       example and private site overlays
web/         static UI
```
