# rackpatch

rackpatch is a compose-first homelab maintenance appliance for Docker stacks, Debian and Ubuntu guests, and Proxmox workflows.

Version in this repo: `v0.2.0`

The main runtime is:

- `ops-web`: rackpatch web UI
- `ops-api`: backend API
- `ops-worker`: database-backed job runner
- `ops-db`: Postgres
- `ops-telegram`: Telegram bot for remote job control
- `ops-notify`: optional send-only Telegram/webhook notifier
- `ops-agent`: remote polling agent for Debian, Ubuntu, and Docker hosts

## Quick Start

```bash
cd /srv/compose/rackpatch
cp .env.example .env
docker compose up -d --build
```

The UI is exposed on:

- `http://<host>:3011`

Default bootstrap login:

- username: `opsadmin`
- password: value of `OPS_ADMIN_PASSWORD`

If `OPS_AGENT_BOOTSTRAP_TOKEN=bootstrap-me`, rackpatch generates a stable bootstrap token on first start and shows it in `Settings`.

## Refresh After Changes

Full refresh:

```bash
cd /srv/compose/rackpatch
docker compose up -d --build
docker compose ps
```

Targeted refreshes:

```bash
# Frontend only
docker compose up -d --build ops-web

# API / worker / Telegram bot
docker compose up -d --build ops-api ops-worker ops-telegram

# Logs
docker compose logs -f ops-api ops-worker ops-web ops-telegram
```

If you changed `.env`, rerun `docker compose up -d --build` so containers are recreated with the new environment. If you changed the web UI, do a hard refresh in the browser after redeploying.

## UI Layout

rackpatch now uses page-style navigation instead of one long dashboard:

- `Overview`: health, recent jobs, pending approvals, install commands
- `Stacks`: compact table with discover, dry-run, live update, rollback
- `Hosts`: inventory, agent state, package and Proxmox actions
- `Agents`: registered pollers and capabilities
- `Jobs`: manual job form plus live job logs
- `Approvals`: pending gated work
- `Schedules`: disabled-by-default automation controls
- `Backups`: artifacts and rollback records
- `Settings`: public repo config, Telegram config, install commands, site paths

## v0.2.0 Highlights

- Broke the UI into focused pages instead of one long dashboard.
- Added a mobile-friendly layout for navigation, tables, and install previews.
- Seeded clearer disabled-by-default sample schedules for discovery, package checks, patch approvals, and Proxmox patching.
- Added Telegram notifications for approval requests, approvals, and job completion results.

## Telegram Control

`ops-telegram` uses the same backend jobs and approvals as the UI. Configure the bot token and allowed chat IDs in `Settings`, then use commands like:

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

## Site Overlays

Tracked defaults use the example overlay:

- `OPS_SITE_NAME=example`
- `OPS_SITE_ROOT=/workspace/sites/example`

Start from the example files:

```bash
cp -R sites/example sites/local
```

Then point your local `.env` at your private overlay:

```dotenv
OPS_SITE_NAME=local
OPS_SITE_ROOT=/workspace/sites/local
```

Keep `sites/local` private. It is ignored by git.

## Public Repo Safety

- Commit `.env.example`, never `.env`.
- Keep your real inventory, stacks, and maintenance policy in `sites/local`, not in tracked example files.
- Runtime data, backups, local secrets, key material, and generated state are ignored by `.gitignore`.
- For a public GitHub repo, `.env` is acceptable for local bootstrap on a private host, but long-lived shared secrets should move to Docker secrets or another secret manager before broad distribution.
- Rotate any tokens or passwords from your current local `.env` before your first public push.

## GitHub Publishing

The repo URL for this project is:

- `https://github.com/SchmidtCode/rackpatch`

Use these values in your local `.env`:

```dotenv
OPS_PUBLIC_BASE_URL=http://YOUR-OPS-HOST:3011
OPS_PUBLIC_REPO_URL=https://github.com/SchmidtCode/rackpatch.git
OPS_PUBLIC_REPO_REF=main
OPS_PUBLIC_INSTALL_SCRIPT_URL=
```

`OPS_PUBLIC_INSTALL_SCRIPT_URL` is optional. If left blank and `OPS_PUBLIC_REPO_URL` is a GitHub repo URL, rackpatch derives:

```text
https://raw.githubusercontent.com/<owner>/<repo>/<ref>/scripts/install-agent.sh
```

Rebuild the API and web services so the enrollment commands use the latest public repo settings:

```bash
docker compose up -d --build ops-api ops-web ops-telegram
```

If `origin` already exists, do not add it again. Check it with:

```bash
git remote -v
```

For the `v0.2.0` release, use a normal branch-to-PR flow instead of force-pushing `main`:

```bash
git fetch origin
git switch -c release/v0.2.0
git push -u origin release/v0.2.0
```

Open a pull request from `release/v0.2.0` into `main`, then after merge:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

Suggested GitHub release notes:

- Broke the UI into page-based views.
- Added a mobile-friendly version of the web UI.
- Added seeded sample schedules for common maintenance flows.
- Added Telegram notifications for approvals and job outcomes.

## Agent Enrollment

The UI `Settings` page shows the exact one-line install commands based on:

- `OPS_PUBLIC_BASE_URL`
- `OPS_PUBLIC_REPO_URL`
- `OPS_PUBLIC_REPO_REF`
- `OPS_PUBLIC_AGENT_COMPOSE_DIR`
- the current bootstrap token

Manual example:

```bash
curl -fsSL https://raw.githubusercontent.com/SchmidtCode/rackpatch/main/scripts/install-agent.sh | bash -s -- \
  --server-url http://YOUR-OPS-HOST:3011 \
  --bootstrap-token YOUR_BOOTSTRAP_TOKEN \
  --mode container \
  --install-source https://github.com/SchmidtCode/rackpatch.git \
  --install-ref main
```

Current agent packaging options:

- container mode: installs under `/opt/rackpatch-agent` and starts a compose-managed `rackpatch-agent`
- systemd mode: installs under `/opt/rackpatch-agent` and starts `rackpatch-agent.service`
- Docker Compose mode: shows a copy/paste example that creates `compose.yml` under the configured compose directory and starts the agent with `docker compose`

## Layout

- `app/api/main.py`: FastAPI backend
- `app/worker/main.py`: job runner and schedule loop
- `app/agent/main.py`: polling agent
- `app/telegrambot/main.py`: Telegram control bot
- `app/notify/main.py`: optional notifier
- `web/index.html`: rackpatch UI shell
- `sites/example`: public example site

## Common Commands

```bash
make up
make logs
make worker-logs
make validate
make rollback STACK=dashboard
make backup-legacy
```

## Notes

- Schedules are seeded from the site overlay and remain disabled by default until explicitly enabled in the UI or Telegram.
- Job backups and rollback artifacts are written under `data/backups`.
- `ops-telegram` idles until a bot token is configured.
- `ops-notify` is optional and only sends Telegram messages if a bot token and chat IDs are configured.
