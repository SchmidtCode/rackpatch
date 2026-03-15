#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  update-rackpatch.sh --install-dir DIR --repo-url URL [--ref REF]
EOF
}

install_dir=""
repo_url=""
ref="main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      install_dir="$2"
      shift 2
      ;;
    --repo-url)
      repo_url="$2"
      shift 2
      ;;
    --ref)
      ref="$2"
      shift 2
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${install_dir}" || -z "${repo_url}" ]]; then
  usage
  exit 1
fi

mkdir -p "$(dirname "${install_dir}")"

branch_exists() {
  git ls-remote --exit-code --heads "${repo_url}" "${ref}" >/dev/null 2>&1
}

if [[ ! -d "${install_dir}/.git" ]]; then
  rm -rf "${install_dir}"
  if branch_exists; then
    git clone --depth 1 --branch "${ref}" "${repo_url}" "${install_dir}"
  else
    git clone --depth 1 "${repo_url}" "${install_dir}"
    git -C "${install_dir}" fetch --depth 1 --tags origin "${ref}" >/dev/null 2>&1 || true
    git -C "${install_dir}" checkout "${ref}"
  fi
else
  git -C "${install_dir}" remote set-url origin "${repo_url}"
  git -C "${install_dir}" fetch --prune --tags origin
  if branch_exists; then
    git -C "${install_dir}" checkout -B "${ref}" "origin/${ref}"
  else
    git -C "${install_dir}" checkout "${ref}"
  fi
fi

docker compose -f "${install_dir}/docker-compose.yml" up -d --build --remove-orphans
echo "rackpatch updated in ${install_dir} to ${ref}"
