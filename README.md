# rackpatch

rackpatch is a compose-first homelab maintenance appliance for Docker stacks, helper-gated Debian or Ubuntu package maintenance, and opt-in Proxmox node actions.

Version in this repo: `v0.4.0`

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
- Compose and container agent self-updates now run from a helper container so agents do not replace themselves mid-update and fall offline.

## Runtime services

- `web`: rackpatch web UI on `RACKPATCH_HTTP_PORT` (default `3011`)
- `api`: backend API
- `worker`: database-backed job runner
- `db`: Postgres
- `telegram`: Telegram bot for remote job control
- `notify`: optional send-only Telegram notifier with log-only fallback when Telegram is not configured
- `rackpatch-agent`: remote polling agent for Debian, Ubuntu, and Docker hosts

## Quick start

Choose any working directory you want and reuse it consistently in the commands below.

### Option 1: clone the repo

```bash
RACKPATCH_DIR=/srv/compose/rackpatch

git clone https://github.com/SchmidtCode/rackpatch.git "${RACKPATCH_DIR}"
cp "${RACKPATCH_DIR}/.env.example" "${RACKPATCH_DIR}/.env"
docker compose -f "${RACKPATCH_DIR}/docker-compose.yml" --env-file "${RACKPATCH_DIR}/.env" pull
docker compose -f "${RACKPATCH_DIR}/docker-compose.yml" --env-file "${RACKPATCH_DIR}/.env" up -d --remove-orphans
```

### Option 2: run the published stack without cloning

This path downloads the tracked example `.env` and a published-image compose file into your chosen directory. It uses the example site catalog baked into the published images and persists it in a Docker volume, so it is a good fit for a quick first boot without losing inventory edits on restart.

```bash
RACKPATCH_DIR=/srv/compose/rackpatch

mkdir -p "${RACKPATCH_DIR}"
curl -fsSL https://raw.githubusercontent.com/SchmidtCode/rackpatch/main/.env.example -o "${RACKPATCH_DIR}/.env"
curl -fsSL https://raw.githubusercontent.com/SchmidtCode/rackpatch/main/docker-compose.published.yml -o "${RACKPATCH_DIR}/docker-compose.yml"
docker compose -f "${RACKPATCH_DIR}/docker-compose.yml" --env-file "${RACKPATCH_DIR}/.env" pull
docker compose -f "${RACKPATCH_DIR}/docker-compose.yml" --env-file "${RACKPATCH_DIR}/.env" up -d --remove-orphans
```

The UI is available at `http://<host>:3011`.

Default bootstrap login:

- Username: value of `RACKPATCH_ADMIN_USERNAME` (default `admin`)
- Password: value of `RACKPATCH_ADMIN_PASSWORD`

If `RACKPATCH_AGENT_BOOTSTRAP_TOKEN=bootstrap-me`, rackpatch generates a stable bootstrap token on first start and exposes it in `Settings`.

If you want local site overlays, source builds, or the full repo tooling, use the clone path.

## Agents

After the control plane is up, open `Settings` and use the generated install and helper commands there. That keeps the commands aligned with your live `RACKPATCH_PUBLIC_*` settings and the current bootstrap token.

The generated install blocks now start with `AGENT_DIR=...`, so you can change the directory in one place before you run them.

Under the hood:

- compose-mode agents use `--compose-dir "${AGENT_DIR}"`
- systemd and container-mode agents use `--install-dir "${AGENT_DIR}"`

Optional host-maintenance enablement is a separate generated command on the same page. Run it only on nodes where you want package or Proxmox helper actions exposed.

For Proxmox nodes, replace `--preset packages` with `--preset all` if you want both package and Proxmox capabilities on that node, or `--preset proxmox` if you want only Proxmox patch and reboot actions advertised.

If you are installing a compose-mode agent on a Docker host where Unix sockets are blocked inside containers under the default AppArmor profile, append:

```bash
--security-opt apparmor=unconfined
```

That AppArmor workaround was required on the Proxmox Docker host used during bring-up. Without it, the agent could not open Unix sockets, which prevented both Docker discovery and helper-backed capability detection.

The helper step needs real root access on the managed host because it installs a systemd service, sudoers policy, and helper socket.

## Hosts

Hosts are loaded from the active site inventory at `sites/<site>/inventory/hosts.yml`.

On clone-based installs that means the tracked `./sites/<site>` directory in your checkout. On the published-stack path, the same tree is stored in the persistent `rackpatch-sites-data` Docker volume.

You can now manage common host inventory fields in two ways:

- in the `Hosts` page in the web UI
- through the API with `POST /api/v1/hosts`, `PUT /api/v1/hosts/{host_name}`, and `DELETE /api/v1/hosts/{host_name}`

The UI writes changes back to the active inventory file and preserves existing advanced keys that it does not expose directly.

## Docs

- [Docs index](docs/README.md)
- [Setup and configuration](docs/setup-and-configuration.md)
- [Development](docs/development.md)
- [Agents and host maintenance](docs/agents-and-host-maintenance.md)
- [Automation and control surfaces](docs/automation-and-control-surfaces.md)
- [Release and publishing](docs/release-and-publishing.md)

## Common commands

- `make up`: pull published images and start the stack
- `make dev-up`: run the stack with `docker-compose.dev.yml` source-build overrides
- `make logs`: tail API logs
- `make worker-logs`: tail worker logs
- `make validate`: run validation checks; falls back to the running `api` container if host Python deps are missing
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
