#!/usr/bin/env bash

set -euo pipefail

stamp="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
backup_dir="/srv/compose/ops-backups/pre-custom-ui-v2-${stamp}"

mkdir -p "${backup_dir}"

git bundle create "${backup_dir}/repo.bundle" --all
git status --short --branch > "${backup_dir}/git-status.txt"
git branch --list > "${backup_dir}/git-branches.txt"
git rev-parse HEAD > "${backup_dir}/git-head.txt"

cp .env "${backup_dir}/.env"
tar czf "${backup_dir}/state.tgz" state

docker compose config > "${backup_dir}/docker-compose.rendered.yml"
docker compose ps --format json > "${backup_dir}/docker-compose.ps.json"

for container in ops-controller ops-scheduler ops-semaphore ops-semaphore-db ops-telegram; do
  docker inspect "${container}" > "${backup_dir}/${container}.inspect.json"
done

for volume in ops_semaphore-db ops_semaphore-config ops_semaphore-tmp; do
  docker run --rm \
    -v "${volume}:/source:ro" \
    -v "${backup_dir}:/backup" \
    busybox:1.36 \
    sh -c "tar czf /backup/${volume}.tgz -C /source ."
done

cat > "${backup_dir}/FREEZE-NOTES.txt" <<'EOF'
Legacy automation frozen for custom-ui-v2 migration.
Repo-shipped n8n workflow templates are inactive by default.
Pause any external DIUN or n8n callers before re-enabling automation.
EOF

echo "${backup_dir}"

