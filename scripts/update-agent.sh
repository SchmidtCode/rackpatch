#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  update-agent.sh --mode compose|container|systemd [--compose-dir DIR] [--install-dir DIR] [--stack-root DIR] [--image IMAGE] [--install-source PATH_OR_REPO] [--install-ref REF]
EOF
}

mode=""
compose_dir="/srv/compose/rackpatch-agent"
install_dir="/opt/rackpatch-agent"
image=""
install_source=""
install_ref=""
tmp_root=""
compose_override_name="compose.host-maintenance.yml"
agent_env_name="agent.env"
stack_roots=()

add_stack_root() {
  local value="${1:-}"
  if [[ "${value}" == "/" ]]; then
    value="/"
  else
    value="${value%/}"
  fi
  if [[ -z "${value}" ]]; then
    return
  fi
  local existing
  for existing in "${stack_roots[@]}"; do
    if [[ "${existing}" == "${value}" ]]; then
      return
    fi
  done
  stack_roots+=("${value}")
}

stack_roots_env() {
  local IFS=,
  printf '%s\n' "${stack_roots[*]}"
}

import_stack_roots_from_compose() {
  local target_dir="$1"
  local current=""
  if [[ ! -f "${target_dir}/compose.yml" ]]; then
    return
  fi
  current="$(read_compose_env_value "${target_dir}" "RACKPATCH_AGENT_STACK_ROOTS")"
  IFS=',' read -r -a current_roots <<< "${current}"
  local root
  for root in "${current_roots[@]}"; do
    add_stack_root "${root}"
  done
}

compose_has_stack_root_config() {
  local target_dir="$1"
  grep -q '^[[:space:]]*RACKPATCH_AGENT_STACK_ROOTS:' "${target_dir}/compose.yml"
}

if [[ -d /srv/compose ]]; then
  add_stack_root /srv/compose
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      mode="$2"
      shift 2
      ;;
    --compose-dir)
      compose_dir="$2"
      shift 2
      ;;
    --install-dir)
      install_dir="$2"
      shift 2
      ;;
    --stack-root)
      add_stack_root "$2"
      shift 2
      ;;
    --image)
      image="$2"
      shift 2
      ;;
    --install-source)
      install_source="$2"
      shift 2
      ;;
    --install-ref)
      install_ref="$2"
      shift 2
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${mode}" ]]; then
  usage
  exit 1
fi

cleanup() {
  if [[ -n "${tmp_root}" && -d "${tmp_root}" ]]; then
    rm -rf "${tmp_root}"
  fi
}
trap cleanup EXIT

default_install_source() {
  if [[ -n "${install_source}" ]]; then
    return
  fi
  if [[ -z "${BASH_SOURCE[0]-}" ]]; then
    return
  fi
  local candidate
  candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if [[ -f "${candidate}/Dockerfile.agent" ]]; then
    install_source="${candidate}"
  fi
}

github_repo_slug() {
  local value="${1:-}"
  value="${value%/}"
  if [[ "${value}" =~ ^https?://github\.com/([^/]+)/([^/]+)(\.git)?$ ]]; then
    printf '%s/%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi
  if [[ "${value}" =~ ^ssh://git@github\.com/([^/]+)/([^/]+)(\.git)?$ ]]; then
    printf '%s/%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi
  if [[ "${value}" =~ ^git@github\.com:([^/]+)/([^/]+)(\.git)?$ ]]; then
    printf '%s/%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi
  return 1
}

normalize_image_tag() {
  local ref="${1:-}"
  ref="${ref#v}"
  ref="${ref#V}"
  if [[ -z "${ref}" || "${ref}" == "main" || "${ref}" == "master" ]]; then
    printf 'latest\n'
    return 0
  fi
  if [[ "${ref}" =~ ^[A-Za-z0-9._-]+$ ]]; then
    printf '%s\n' "${ref}"
    return 0
  fi
  printf 'latest\n'
}

derive_agent_image() {
  if [[ -n "${image}" ]]; then
    printf '%s\n' "${image}"
    return 0
  fi
  local slug
  if ! slug="$(github_repo_slug "${install_source}")"; then
    return 1
  fi
  local owner="${slug%%/*}"
  printf 'ghcr.io/%s/rackpatch-agent:%s\n' "${owner,,}" "$(normalize_image_tag "${install_ref}")"
}

resolve_source() {
  if [[ -z "${install_source}" ]]; then
    echo "install source is required for source-based updates" >&2
    exit 1
  fi
  if [[ -d "${install_source}" ]]; then
    printf '%s\n' "${install_source}"
    return
  fi
  if [[ "${install_source}" =~ ^https?:// ]] || [[ "${install_source}" =~ \.git$ ]]; then
    tmp_root="$(mktemp -d)"
    if [[ -n "${install_ref}" ]]; then
      git clone --depth 1 --branch "${install_ref}" "${install_source}" "${tmp_root}" >/dev/null 2>&1
    else
      git clone --depth 1 "${install_source}" "${tmp_root}" >/dev/null 2>&1
    fi
    printf '%s\n' "${tmp_root}"
    return
  fi
  echo "install source not found: ${install_source}" >&2
  exit 1
}

compose_args() {
  local target_dir="$1"
  local -a args=(-f "${target_dir}/compose.yml")
  if [[ -f "${target_dir}/${agent_env_name}" ]]; then
    args=(--env-file "${target_dir}/${agent_env_name}" "${args[@]}")
  fi
  if [[ -f "${target_dir}/${compose_override_name}" ]]; then
    args+=(-f "${target_dir}/${compose_override_name}")
  fi
  printf '%s\n' "${args[@]}"
}

run_compose() {
  local target_dir="$1"
  shift
  local -a args=()
  while IFS= read -r line; do
    args+=("${line}")
  done < <(compose_args "${target_dir}")
  docker compose "${args[@]}" "$@"
}

compose_uses_build() {
  local target_dir="$1"
  grep -q '^[[:space:]]*build:' "${target_dir}/compose.yml"
}

write_image_env() {
  local target_dir="$1"
  local agent_image="$2"
  cat > "${target_dir}/${agent_env_name}" <<EOF
RACKPATCH_AGENT_IMAGE=${agent_image}
EOF
}

prepare_source_context() {
  local src_root="$1"
  local target_dir="$2"
  mkdir -p "${target_dir}/src"
  rm -rf "${target_dir}/src/app"
  cp -R "${src_root}/app" "${target_dir}/src/app"
  cp "${src_root}/Dockerfile.agent" "${target_dir}/src/Dockerfile.agent"
  cp "${src_root}/requirements-rackpatch.txt" "${target_dir}/src/requirements-rackpatch.txt"
}

compose_uses_image_env() {
  local target_dir="$1"
  grep -q 'RACKPATCH_AGENT_IMAGE' "${target_dir}/compose.yml"
}

read_compose_env_value() {
  local target_dir="$1"
  local key="$2"
  local line=""
  line="$(grep -m1 "^[[:space:]]*${key}:" "${target_dir}/compose.yml" || true)"
  line="${line#*:}"
  line="${line# }"
  printf '%s\n' "${line}"
}

rewrite_image_compose() {
  local target_dir="$1"
  local runtime_mode="$2"
  local state_mount="$3"
  local server_url
  local bootstrap_token
  local agent_name
  local agent_labels
  local stack_roots_csv

  server_url="$(read_compose_env_value "${target_dir}" "RACKPATCH_SERVER_URL")"
  bootstrap_token="$(read_compose_env_value "${target_dir}" "RACKPATCH_AGENT_BOOTSTRAP_TOKEN")"
  agent_name="$(read_compose_env_value "${target_dir}" "RACKPATCH_AGENT_NAME")"
  agent_labels="$(read_compose_env_value "${target_dir}" "RACKPATCH_AGENT_LABELS")"
  stack_roots_csv="$(stack_roots_env)"

  if [[ -z "${server_url}" || -z "${bootstrap_token}" ]]; then
    echo "could not migrate ${target_dir}/compose.yml to image mode automatically" >&2
    exit 1
  fi

  cat > "${target_dir}/compose.yml" <<EOF
services:
  rackpatch-agent:
    container_name: rackpatch-agent
    image: \${RACKPATCH_AGENT_IMAGE}
    restart: unless-stopped
    environment:
      RACKPATCH_SERVER_URL: ${server_url}
      RACKPATCH_AGENT_BOOTSTRAP_TOKEN: ${bootstrap_token}
      RACKPATCH_AGENT_NAME: ${agent_name}
      RACKPATCH_AGENT_LABELS: ${agent_labels}
      RACKPATCH_AGENT_MODE: ${runtime_mode}
      RACKPATCH_AGENT_STATE_DIR: /var/lib/rackpatch-agent
      RACKPATCH_AGENT_STACK_ROOTS: ${stack_roots_csv}
EOF
  if [[ "${runtime_mode}" == "compose" ]]; then
    cat >> "${target_dir}/compose.yml" <<EOF
      RACKPATCH_AGENT_COMPOSE_DIR: ${target_dir}
EOF
  else
    cat >> "${target_dir}/compose.yml" <<EOF
      RACKPATCH_AGENT_INSTALL_DIR: ${target_dir}
EOF
  fi
  cat >> "${target_dir}/compose.yml" <<EOF
    volumes:
      - ${state_mount}:/var/lib/rackpatch-agent
      - /var/run/docker.sock:/var/run/docker.sock
EOF
  local stack_root
  for stack_root in "${stack_roots[@]}"; do
    cat >> "${target_dir}/compose.yml" <<EOF
      - ${stack_root}:${stack_root}
EOF
  done
}

default_install_source

case "${mode}" in
  compose)
    target_dir="${compose_dir}"
    mkdir -p "${target_dir}"
    import_stack_roots_from_compose "${target_dir}"
    agent_image="$(derive_agent_image || true)"
    if [[ -n "${agent_image}" ]]; then
      if [[ -f "${target_dir}/compose.yml" ]] && { ! compose_uses_image_env "${target_dir}" || ! compose_has_stack_root_config "${target_dir}"; }; then
        rewrite_image_compose "${target_dir}" "compose" "./state"
      fi
      write_image_env "${target_dir}" "${agent_image}"
      run_compose "${target_dir}" pull rackpatch-agent
      run_compose "${target_dir}" up -d
    else
      src_root="$(resolve_source)"
      prepare_source_context "${src_root}" "${target_dir}"
      if [[ ! -f "${target_dir}/${agent_env_name}" ]]; then
        write_image_env "${target_dir}" "rackpatch-agent:local"
      fi
      if compose_uses_build "${target_dir}"; then
        run_compose "${target_dir}" up -d --build
      else
        run_compose "${target_dir}" up -d
      fi
    fi
    echo "rackpatch agent updated in compose mode under ${target_dir}"
    ;;
  container)
    target_dir="${install_dir}"
    mkdir -p "${target_dir}"
    import_stack_roots_from_compose "${target_dir}"
    agent_image="$(derive_agent_image || true)"
    if [[ -n "${agent_image}" ]]; then
      if [[ -f "${target_dir}/compose.yml" ]] && { ! compose_uses_image_env "${target_dir}" || ! compose_has_stack_root_config "${target_dir}"; }; then
        rewrite_image_compose "${target_dir}" "container" "/var/lib/rackpatch-agent"
      fi
      write_image_env "${target_dir}" "${agent_image}"
      run_compose "${target_dir}" pull rackpatch-agent
      run_compose "${target_dir}" up -d
    else
      src_root="$(resolve_source)"
      prepare_source_context "${src_root}" "${target_dir}"
      if [[ ! -f "${target_dir}/${agent_env_name}" ]]; then
        write_image_env "${target_dir}" "rackpatch-agent:local"
      fi
      if compose_uses_build "${target_dir}"; then
        run_compose "${target_dir}" up -d --build
      else
        run_compose "${target_dir}" up -d
      fi
    fi
    echo "rackpatch agent updated in container mode under ${target_dir}"
    ;;
  systemd)
    src_root="$(resolve_source)"
    mkdir -p "${install_dir}"
    rm -rf "${install_dir}/app"
    cp -R "${src_root}/app" "${install_dir}/app"
    cp "${src_root}/requirements-rackpatch.txt" "${install_dir}/requirements-rackpatch.txt"
    "${install_dir}/venv/bin/pip" install -r "${install_dir}/requirements-rackpatch.txt"
    if id -u rackpatch-agent >/dev/null 2>&1; then
      chown -R rackpatch-agent:rackpatch-agent "${install_dir}/app" "${install_dir}/requirements-rackpatch.txt"
    fi
    systemctl restart rackpatch-agent.service
    echo "rackpatch agent updated in systemd mode under ${install_dir}"
    ;;
  *)
    echo "unsupported mode: ${mode}" >&2
    usage
    exit 1
    ;;
esac
