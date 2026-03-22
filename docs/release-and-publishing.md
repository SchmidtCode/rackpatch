# Release and publishing

## Public repo safety

- Commit `.env.example`, never `.env`.
- Keep real inventory, stacks, and maintenance policy in a private `sites/<name>` overlay, not in tracked example files.
- Runtime data, backups, secrets, key material, and generated state are ignored by both `.gitignore` and `.dockerignore`.
- Run `make release-check` before pushing a public branch. It fails if tracked files include `.env`, key material, `secrets/`, or non-example site overlays.
- Rotate any tokens or passwords from your current local `.env` before the first public push.

## GitHub Actions and GHCR

GitHub automation builds and publishes the three custom images, and the tracked `docker-compose.yml` uses those published images by default. The tracked `docker-compose.dev.yml` is the source-build override for local development.

GitHub automation has two jobs under `.github/workflows/`:

- `ci.yml`: runs `make validate` and verifies that the three custom images build on pull requests and pushes to `main`
- `publish-images.yml`: publishes versioned images to GitHub Container Registry when you push a tag like `v0.3.7`

Published image names:

- `ghcr.io/<owner>/rackpatch`
- `ghcr.io/<owner>/rackpatch-agent`
- `ghcr.io/<owner>/rackpatch-web`

Suggested first publish flow:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git tag -a v0.3.7 -m "v0.3.7"
git push origin refs/tags/v0.3.7
```

After the first publish, open the package pages in GitHub and set them to public if you want anonymous pulls from GHCR.

## Release flow for `v0.3.7`

If `origin` is already configured, confirm it first:

```bash
git remote -v
```

Push the release branch:

```bash
git fetch origin
git switch -c release/v0.3.7
git push -u origin release/v0.3.7
```

Open a pull request from `release/v0.3.7` into `main`. After the PR merges:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git tag -a v0.3.7 -m "v0.3.7"
git push origin refs/tags/v0.3.7
```

Suggested GitHub release notes:

- Page-based UI with mobile-friendly layouts.
- Telegram notifications for approvals and job completion results.
- Backend-generated install and update commands plus job-kind metadata.
- Machine-readable control-plane context for AI operators.
- GitHub release tracking for the stack and enrolled agents.
- GHCR-backed default deployments plus a tracked `docker-compose.dev.yml` for local source builds.
- Safer public GitHub publishing with `make release-check`.
- Docker live updates now support lightweight stack-directory backups with retention controls.
