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

normalize_image_tag() {
  local value="${1:-}"
  value="${value#v}"
  value="${value#V}"
  if [[ -z "${value}" || "${value}" == "main" || "${value}" == "master" ]]; then
    printf 'latest\n'
    return 0
  fi
  if [[ "${value}" =~ ^[A-Za-z0-9._-]+$ ]]; then
    printf '%s\n' "${value}"
    return 0
  fi
  printf 'latest\n'
}

set_env_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  if [[ ! -f "${file}" ]]; then
    touch "${file}"
  fi
  if grep -q "^${key}=" "${file}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${file}"
  fi
}

compose_args() {
  local -a args=(-f "${install_dir}/docker-compose.yml")
  if [[ -f "${install_dir}/.env" ]]; then
    args=(--env-file "${install_dir}/.env" "${args[@]}")
  fi
  printf '%s\n' "${args[@]}"
}

run_compose() {
  local -a args=()
  while IFS= read -r line; do
    args+=("${line}")
  done < <(compose_args)
  docker compose "${args[@]}" "$@"
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

if [[ ! -f "${install_dir}/.env" && -f "${install_dir}/.env.example" ]]; then
  cp "${install_dir}/.env.example" "${install_dir}/.env"
fi
if [[ -f "${install_dir}/.env" ]]; then
  set_env_value "${install_dir}/.env" "RACKPATCH_VERSION" "$(normalize_image_tag "${ref}")"
fi

run_compose pull
run_compose up -d --remove-orphans
echo "rackpatch updated in ${install_dir} to ${ref}"
