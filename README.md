# Custom Ops UI

Custom Ops UI is a compose-first homelab maintenance appliance for Docker stacks, Debian/Ubuntu guests, and Proxmox workflows.

Version in this repo: `v0.1.0`

The main runtime is:

- `ops-web`: custom web UI
- `ops-api`: backend API
- `ops-worker`: database-backed job runner
- `ops-db`: Postgres
- `ops-notify`: optional Telegram/webhook adapter
- `ops-agent`: remote polling agent for Debian/Ubuntu and Docker hosts

## Quick Start

```bash
cd /srv/compose/ops
cp .env.example .env
docker compose up -d --build
```

The UI is exposed on:

- `http://<host>:3011`

Default bootstrap login:

- username: `opsadmin`
- password: value of `OPS_ADMIN_PASSWORD`

If `OPS_AGENT_BOOTSTRAP_TOKEN=bootstrap-me`, the app generates a stable bootstrap token on first start and shows it in `Settings`.

## Refresh After Changes

Full refresh:

```bash
cd /srv/compose/ops
docker compose up -d --build
docker compose ps
```

Targeted refreshes:

```bash
# Frontend only
docker compose up -d --build ops-web

# API / worker code
docker compose up -d --build ops-api ops-worker

# Logs
docker compose logs -f ops-api ops-worker ops-web
```

If you changed `.env`, rerun `docker compose up -d --build` so containers are recreated with the new environment. If you changed the web UI, do a hard refresh in the browser after redeploying.

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

1. Create a new GitHub repository.
2. Choose the branch you want to publish as the public root branch.
3. Set these values in your local `.env` before rebuilding:

```dotenv
OPS_PUBLIC_BASE_URL=http://YOUR-OPS-HOST:3011
OPS_PUBLIC_REPO_URL=https://github.com/YOUR-ORG/YOUR-REPO.git
OPS_PUBLIC_REPO_REF=v0.1.0
OPS_PUBLIC_INSTALL_SCRIPT_URL=
```

`OPS_PUBLIC_INSTALL_SCRIPT_URL` is optional. If left blank and `OPS_PUBLIC_REPO_URL` is a GitHub repo URL, the UI derives the installer URL automatically as:

```text
https://raw.githubusercontent.com/<owner>/<repo>/<ref>/scripts/install-agent.sh
```

4. Rebuild the API and web services so the enrollment command in the UI uses your GitHub repo:

```bash
docker compose up -d --build ops-api ops-web
```

5. Add your GitHub remote and push:

```bash
git remote add origin git@github.com:YOUR-ORG/YOUR-REPO.git
git push -u origin YOUR_PUBLIC_BRANCH:main
git push origin v0.1.0
```

6. In GitHub:

- set the default branch to `main`
- add a repo description
- add a `v0.1.0` release from the tag
- enable Issues if you want community bug reports
- optionally add Discussions for homelab setup questions

## Agent Enrollment

The UI `Settings` page shows the exact one-line install commands based on:

- `OPS_PUBLIC_BASE_URL`
- `OPS_PUBLIC_REPO_URL`
- `OPS_PUBLIC_REPO_REF`
- the current bootstrap token

Manual example:

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR-ORG/YOUR-REPO/v0.1.0/scripts/install-agent.sh | sh -s -- \
  --server-url http://YOUR-OPS-HOST:3011 \
  --bootstrap-token YOUR_BOOTSTRAP_TOKEN \
  --mode container \
  --install-source https://github.com/YOUR-ORG/YOUR-REPO.git
```

Current agent packaging options:

- container mode: builds and runs the local `Dockerfile.agent`
- systemd mode: installs a small Python venv and runs `agent.main`

## Layout

- `app/api/main.py`: FastAPI backend
- `app/worker/main.py`: job runner and schedule loop
- `app/agent/main.py`: polling agent
- `app/notify/main.py`: optional notifier
- `web/index.html`: custom UI shell
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

- Schedules are seeded from the site overlay and remain disabled by default until explicitly enabled in the UI.
- Job backups and rollback artifacts are written under `data/backups`.
- `ops-notify` is optional and only sends Telegram messages if `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS` are set.
