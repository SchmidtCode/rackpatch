# rackpatch

rackpatch is a compose-first homelab maintenance appliance for Docker stacks, Debian and Ubuntu guests, and Proxmox workflows.

Version in this repo: `v0.3.2`

## What rackpatch does

- Tracks Docker stacks from your site catalog and discovered compose projects.
- Runs image discovery, stack updates, backups, rollback capture, and rollback execution.
- Handles guest package checks, guest patching, snapshots, and Proxmox patch/reboot workflows.
- Provides a web UI, Telegram control surface, generated install/update commands, and machine-readable API context.
- Surfaces release status for the control plane and enrolled agents when the public repo points at GitHub.

## v0.3.2 highlights

- Page-based UI with focused `Overview`, `Stacks`, `Hosts`, `Agents`, `Jobs`, `Approvals`, `Schedules`, `Backups`, and `Settings` views.
- Mobile-friendly navigation, tables, and install/update previews.
- Backend-generated job-kind metadata and install/update commands shared by the UI and automation.
- Machine-readable `/api/v1/context` and `/api/v1/job-kinds` endpoints for AI operators and scripted setup.
- GitHub-backed latest-version checks for the control plane and agents.
- Safer public-repo prep with `make release-check` plus stricter ignore rules for secrets and private overlays.
- Helper enable commands now use `sudo` in generated install previews and docs.
- Compose and container helper installs now manage a stable runtime socket directory so host-maintenance helper access survives normal restarts and cleanly recreates on boot.
- Agent heartbeats refresh helper-backed capabilities so the Hosts and Jobs UI stops lagging behind real helper access.

## Runtime services

- `web`: rackpatch web UI, container `rackpatch-web`
- `api`: backend API, container `rackpatch-api`
- `worker`: database-backed job runner, container `rackpatch-worker`
- `db`: Postgres, container `rackpatch-db`
- `telegram`: Telegram bot for remote job control, container `rackpatch-telegram`
- `notify`: optional send-only Telegram/webhook notifier, container `rackpatch-notify`
- `rackpatch-agent`: remote polling agent for Debian, Ubuntu, and Docker hosts

## Quick start

```bash
cd /srv/compose/rackpatch
cp .env.example .env
docker compose up -d --build --remove-orphans
```

The UI is available at `http://<host>:3011`.

Default bootstrap login:

- Username: value of `RACKPATCH_ADMIN_USERNAME` (default `admin`)
- Password: value of `RACKPATCH_ADMIN_PASSWORD`

Fresh installs default to `RACKPATCH_DB_VOLUME=rackpatch-db-data` with `RACKPATCH_DB_VOLUME_EXTERNAL=false`.

If you want to point rackpatch at a pre-created Docker volume, set both values explicitly:

```bash
RACKPATCH_DB_VOLUME=your-existing-volume
RACKPATCH_DB_VOLUME_EXTERNAL=true
```

If `RACKPATCH_AGENT_BOOTSTRAP_TOKEN=bootstrap-me`, rackpatch generates a stable bootstrap token on first start and exposes it in `Settings`.

## Common refresh commands

```bash
docker compose up -d --build --remove-orphans
docker compose up -d --build web
docker compose up -d --build api worker telegram
docker compose logs -f api worker web telegram
```

If you change `.env`, rerun `docker compose up -d --build --remove-orphans` so containers are recreated with the new environment.

## Site overlays

Tracked defaults use the example overlay:

- `RACKPATCH_SITE_NAME=example`
- `RACKPATCH_SITE_ROOT=/workspace/sites/example`

To create a private overlay:

```bash
cp -R sites/example sites/local
```

Then point `.env` at it:

```dotenv
RACKPATCH_SITE_NAME=local
RACKPATCH_SITE_ROOT=/workspace/sites/local
```

Any `sites/*` overlay except `sites/example` is ignored by both git and the Docker build context.

If the control-plane host is also present in inventory, set `rackpatch_control_plane: true` on that host so the `Hosts` page marks it correctly even when the public URL hostname and inventory address do not match exactly.

## Public repo and GitHub settings

For public install/update command generation, set:

```dotenv
RACKPATCH_PUBLIC_BASE_URL=http://YOUR-RACKPATCH-HOST:3011
RACKPATCH_PUBLIC_REPO_URL=https://github.com/SchmidtCode/rackpatch.git
RACKPATCH_PUBLIC_REPO_REF=main
RACKPATCH_PUBLIC_INSTALL_SCRIPT_URL=
RACKPATCH_PUBLIC_AGENT_COMPOSE_DIR=/srv/compose/rackpatch-agent
RACKPATCH_PUBLIC_RACKPATCH_COMPOSE_DIR=/srv/compose/rackpatch
RACKPATCH_CORS_ORIGINS=
```

Notes:

- `RACKPATCH_PUBLIC_REPO_URL` can be a GitHub HTTPS URL or GitHub SSH URL such as `git@github.com:SchmidtCode/rackpatch.git`.
- If `RACKPATCH_PUBLIC_INSTALL_SCRIPT_URL` is blank, rackpatch derives raw GitHub script URLs automatically.
- `RACKPATCH_PUBLIC_AGENT_COMPOSE_DIR` now defaults to `/srv/compose/rackpatch-agent` so compose-mode agent installs do not target the main rackpatch stack directory.
- `RACKPATCH_PUBLIC_RACKPATCH_COMPOSE_DIR` stays `/srv/compose/rackpatch` for control-plane updates.

After changing public repo settings, rebuild the services that expose generated commands:

```bash
docker compose up -d --build api web telegram
```

## Agent install and update flows

The `Settings` page exposes exact generated commands for agent install and update workflows. Those commands are built from:

- `RACKPATCH_PUBLIC_BASE_URL`
- `RACKPATCH_PUBLIC_REPO_URL`
- `RACKPATCH_PUBLIC_REPO_REF`
- `RACKPATCH_PUBLIC_AGENT_COMPOSE_DIR`
- `RACKPATCH_PUBLIC_RACKPATCH_COMPOSE_DIR`
- the current bootstrap token

Agent packaging modes:

- `compose`: installs under the configured agent compose directory and runs with `docker compose`
- `container`: installs under `/opt/rackpatch-agent` and runs a compose-managed `rackpatch-agent:local`
- `systemd`: installs under `/opt/rackpatch-agent` and runs `rackpatch-agent.service`

Container-mode updates explicitly rebuild `rackpatch-agent:local` before redeploying, so agent code changes are not skipped during updates.

Host maintenance is a separate opt-in step. The base agent install stays focused on enrollment and unprivileged operations. If you want limited host package maintenance, run the dedicated helper enable script after the agent is installed.

The web UI treats package check and package patch as helper-gated actions. Hosts without the limited host-maintenance helper stay visible, but their package actions and package-job picker entries are greyed out until that access is enabled.
Package maintenance no longer falls back to the legacy worker or SSH path. Multi-host package requests fan out into one helper-backed agent job per eligible host.

Example container install:

```bash
curl -fsSL https://raw.githubusercontent.com/SchmidtCode/rackpatch/main/scripts/install-agent.sh | bash -s -- \
  --server-url http://YOUR-RACKPATCH-HOST:3011 \
  --bootstrap-token YOUR_BOOTSTRAP_TOKEN \
  --mode container \
  --install-source https://github.com/SchmidtCode/rackpatch.git \
  --install-ref main
```

Example stack update:

```bash
curl -fsSL https://raw.githubusercontent.com/SchmidtCode/rackpatch/v0.3.2/scripts/update-rackpatch.sh | bash -s -- \
  --install-dir /srv/compose/rackpatch \
  --repo-url https://github.com/SchmidtCode/rackpatch.git \
  --ref v0.3.2
```

Example host-maintenance enablement:

```bash
curl -fsSL https://raw.githubusercontent.com/SchmidtCode/rackpatch/main/scripts/enable-agent-host-maintenance.sh | sudo bash -s -- \
  --mode compose \
  --compose-dir /srv/compose/rackpatch-agent \
  --install-source https://github.com/SchmidtCode/rackpatch.git \
  --install-ref main
```

The helper exposes only approved host-maintenance actions and is intended for package check and package patch in this rollout.

`Patch Live` remains grey for helper-backed hosts that still require snapshot-before-patch. In the current rollout, helper-backed live patching only becomes eligible when the host advertises helper access and its inventory policy allows live patching without a pre-patch snapshot, which means `snapshot_class: none` for that host.

## Trust-sensitive privileged actions

- Base agent installs do not enable privileged host maintenance by default.
- Privileged host maintenance is enabled only by the dedicated helper setup step.
- The helper is limited to named maintenance actions such as package check and package patch.
- The helper does not accept arbitrary shell, free-form commands, package names, or paths from the control plane.
- Package check and package patch in the web UI are intentionally disabled on hosts that do not advertise the matching helper-backed capability.
- Package maintenance is agent-only now; if a host cannot satisfy helper or policy requirements, rackpatch rejects or skips that host instead of falling back to worker or SSH execution.
- Every future privileged action must have:
  a named helper action, a dedicated root-owned wrapper, an explicit capability, and UI disclosure.
- Docker socket access is still a separate trust-sensitive capability and will be hardened in a later phase.

## Release tracking and AI/API access

When `RACKPATCH_PUBLIC_REPO_URL` points at GitHub, rackpatch compares the latest upstream release or tag to:

- the running control-plane version
- each enrolled agent version

That release data appears in:

- `Overview`
- `Agents`
- `Settings`
- `/api/v1/settings`
- `/api/v1/context`

Machine-friendly endpoints:

- `/api/v1/context`: settings, release status, install commands, API paths, running jobs, pending approvals, and supported job kinds
- `/api/v1/job-kinds`: job form metadata
- `/api/v1/jobs`: recent jobs
- `/api/v1/jobs/<job-id>/events`: job logs
- `/api/v1/settings`: public repo and Telegram settings

If you need browser access from another origin, set `RACKPATCH_CORS_ORIGINS` to a comma-separated allowlist. The default posture is same-origin only.

## Telegram control

Configure the bot token and allowed chat IDs in `Settings`, then use commands such as:

```text
/status
/stacks
/hosts
/jobs
/approvals
/approve <job-id>
/discover <stack|all>
/update <stack|all> [dry|live]
/patch <host|all> [dry|live]
/snapshot <host>
/proxmox-patch <limit> [dry|live]
/proxmox-reboot <limit> [dry|live]
/backup <volume>
/rollback <stack>
/schedules
/schedule <name-or-id> on|off
/job <kind> <target_type> <target_ref> {"executor":"auto"}
```

## Public repo safety

- Commit `.env.example`, never `.env`.
- Keep real inventory, stacks, and maintenance policy in a private `sites/<name>` overlay, not in tracked example files.
- Runtime data, backups, secrets, key material, and generated state are ignored by both `.gitignore` and `.dockerignore`.
- Run `make release-check` before pushing a public branch. It fails if tracked files include `.env`, key material, `secrets/`, or non-example site overlays.
- Rotate any tokens or passwords from your current local `.env` before the first public push.

## Release flow for v0.3.2

If `origin` is already configured, confirm it first:

```bash
git remote -v
```

Push the release branch:

```bash
git fetch origin
git switch -c release/v0.3.2
git push -u origin release/v0.3.2
```

Open a pull request from `release/v0.3.2` into `main`. After the PR merges:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git tag -a v0.3.2 -m "v0.3.2"
git push origin v0.3.2
```

Suggested GitHub release notes:

- Page-based UI with mobile-friendly layouts.
- Telegram notifications for approvals and job completion results.
- Backend-generated install/update commands and job-kind metadata.
- Machine-readable control-plane context for AI operators.
- GitHub release tracking for the stack and enrolled agents.
- Safer public GitHub publishing with `make release-check`.

## Helpful commands

```bash
make up
make logs
make worker-logs
make validate
make release-check
make rollback STACK=dashboard
make backup-legacy
```

## Repository layout

```text
app/         Python services
web/         static UI
scripts/     release, validation, and operator utilities
playbooks/   Ansible playbooks
roles/       Ansible roles
sites/       example and private site overlays
data/        runtime state, backups, and job artifacts
```

## Notes

- Schedules are seeded from the site overlay and remain disabled by default until explicitly enabled in the UI or Telegram.
- Job backups and rollback artifacts are written under `data/backups`.
- `telegram` idles until a bot token is configured.
- `notify` is optional and only sends Telegram messages when a bot token and chat IDs are configured.
