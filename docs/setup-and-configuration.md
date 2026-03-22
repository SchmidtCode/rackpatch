# Setup and configuration

## Base install

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

## Key `.env` settings

- `RACKPATCH_VERSION` and `RACKPATCH_IMAGE_NAMESPACE` control which published images the main stack pulls.
- `RACKPATCH_HTTP_PORT`, `RACKPATCH_API_PORT`, and `RACKPATCH_NOTIFY_PORT` control the exposed service ports.
- `RACKPATCH_ADMIN_USERNAME`, `RACKPATCH_ADMIN_PASSWORD`, and `RACKPATCH_AUTH_SECRET` should be changed from the tracked defaults before real use.
- `RACKPATCH_AGENT_BOOTSTRAP_TOKEN` seeds agent enrollment. Leaving it at `bootstrap-me` makes rackpatch generate a stable token on first boot.
- `RACKPATCH_DB_VOLUME` and `RACKPATCH_DB_VOLUME_EXTERNAL` control whether Postgres uses the default local volume or a pre-created external one.

Fresh installs default to:

```dotenv
RACKPATCH_DB_VOLUME=rackpatch-db-data
RACKPATCH_DB_VOLUME_EXTERNAL=false
```

If you want to point rackpatch at a pre-created Docker volume, set both values explicitly:

```dotenv
RACKPATCH_DB_VOLUME=your-existing-volume
RACKPATCH_DB_VOLUME_EXTERNAL=true
```

## Common refresh commands

```bash
docker compose pull
docker compose up -d --remove-orphans
docker compose up -d web
docker compose up -d api worker telegram
docker compose logs -f api worker web telegram
```

If you change `.env`, rerun `docker compose up -d --remove-orphans` so containers are recreated with the new environment.

The control-plane stack no longer mounts host SSH material into the containers. The intended path is agent-first Docker maintenance, with optional host-maintenance helper enablement for package work.

For local source builds while developing on the repo:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build --remove-orphans
```

## Site overlays

Tracked defaults use the example overlay:

```dotenv
RACKPATCH_SITE_NAME=example
RACKPATCH_SITE_ROOT=/opt/rackpatch/sites/example
```

To create a private overlay:

```bash
cp -R sites/example sites/local
```

Then point `.env` at it:

```dotenv
RACKPATCH_SITE_NAME=local
RACKPATCH_SITE_ROOT=/opt/rackpatch/sites/local
```

Any `sites/*` overlay except `sites/example` is ignored by both git and the Docker build context.

If the control-plane host is also present in inventory, set `rackpatch_control_plane: true` on that host so the `Hosts` page marks it correctly even when the public URL hostname and inventory address do not match exactly.

## Public repo and GitHub settings

For public install and update command generation, set:

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
- `RACKPATCH_PUBLIC_AGENT_COMPOSE_DIR` defaults to `/srv/compose/rackpatch-agent` so compose-mode agent installs do not target the main rackpatch stack directory.
- `RACKPATCH_PUBLIC_RACKPATCH_COMPOSE_DIR` stays `/srv/compose/rackpatch` for control-plane updates.

After changing public repo settings, rebuild the services that expose generated commands:

```bash
docker compose up -d api web telegram
```
