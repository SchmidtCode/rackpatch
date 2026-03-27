# Development

## Clone and branch

```bash
git clone https://github.com/SchmidtCode/rackpatch.git
cd rackpatch
git switch -c your-branch-name
```

## Local config

Start from the tracked example file:

```bash
cp .env.example .env
```

For local development, a private site overlay is usually more useful than editing the tracked example:

```bash
cp -R sites/example sites/local
```

Then point `.env` at it:

```dotenv
RACKPATCH_SITE_NAME=local
RACKPATCH_SITE_ROOT=/opt/rackpatch/sites/local
```

## Main development loop

Source-based local development uses the tracked override file:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build --remove-orphans
```

Equivalent Make target:

```bash
make dev-up
```

The dev override mounts the repo into the Python services at `/workspace`, so code edits come from your checkout instead of the published image.

## Useful commands

Rebuild the whole dev stack:

```bash
make dev-build
make dev-up
```

Restart only the Python services after code changes:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build api worker telegram
```

Rebuild the web UI after frontend changes:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build web
```

Tail logs:

```bash
make logs
make worker-logs
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f web telegram
```

Open a shell in the API container:

```bash
make shell
```

Stop the stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

## Validation

Run the repo validation bundle before committing:

```bash
make validate
```

Check that the repo is safe to publish publicly:

```bash
make release-check
```

## Notes

- `make validate` falls back to the running `api` container if host Python dependencies are missing.
- The tracked `docker-compose.yml` uses published images by default. The `docker-compose.dev.yml` overlay is the normal local development path.
- If you change `.env`, rerun `docker compose ... up -d --remove-orphans` so the containers pick up the new environment.
- For host inventory work, use `sites/local` or another private overlay rather than editing tracked example inventory directly.
