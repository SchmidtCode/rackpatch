#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install-agent.sh --server-url URL --bootstrap-token TOKEN --mode compose|container|systemd [--compose-dir DIR] [--stack-root DIR] [--name NAME] [--labels a,b] [--image IMAGE] [--install-source PATH_OR_REPO] [--install-ref REF]
EOF
}

server_url=""
bootstrap_token=""
mode="container"
compose_dir="/srv/compose/rackpatch-agent"
install_dir="/opt/rackpatch-agent"
name="$(hostname)"
labels=""
image=""
install_source=""
install_ref=""
tmp_root=""
systemd_agent_user="rackpatch-agent"
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

if [[ -d /srv/compose ]]; then
  add_stack_root /srv/compose
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-url)
      server_url="$2"
      shift 2
      ;;
    --bootstrap-token)
      bootstrap_token="$2"
      shift 2
      ;;
    --mode)
      mode="$2"
      shift 2
      ;;
    --compose-dir)
      compose_dir="$2"
      shift 2
      ;;
    --stack-root)
      add_stack_root "$2"
      shift 2
      ;;
    --name)
      name="$2"
      shift 2
      ;;
    --labels)
      labels="$2"
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

if [[ -z "${server_url}" || -z "${bootstrap_token}" ]]; then
  usage
  exit 1
fi

ensure_systemd_root() {
  if [[ "${mode}" == "systemd" && ${EUID} -ne 0 ]]; then
    echo "run as root for systemd mode" >&2
    exit 1
  fi
}

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
    echo "install source is required for source-based installs" >&2
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

write_image_env() {
  local target_dir="$1"
  local agent_image="$2"
  cat > "${target_dir}/${agent_env_name}" <<EOF
RACKPATCH_AGENT_IMAGE=${agent_image}
EOF
}

socket_group_id() {
  local socket_path="${1:-}"
  if [[ -z "${socket_path}" || ! -e "${socket_path}" ]]; then
    return 0
  fi
  stat -c '%g' "${socket_path}" 2>/dev/null || true
}

compose_args() {
  local target_dir="$1"
  local -a args=(--env-file "${target_dir}/${agent_env_name}" -f "${target_dir}/compose.yml")
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

prepare_source_context() {
  local src_root="$1"
  local target_dir="$2"
  rm -rf "${target_dir}/src"
  mkdir -p "${target_dir}/src"
  cp -R "${src_root}/app" "${target_dir}/src/app"
  cp "${src_root}/Dockerfile.agent" "${target_dir}/src/Dockerfile.agent"
  cp "${src_root}/requirements-rackpatch.txt" "${target_dir}/src/requirements-rackpatch.txt"
}

write_compose_file() {
  local target_dir="$1"
  local runtime_mode="$2"
  local state_mount="$3"
  local use_build="$4"
  local docker_socket_gid=""
  local stack_roots_csv=""
  docker_socket_gid="$(socket_group_id /var/run/docker.sock)"
  stack_roots_csv="$(stack_roots_env)"

  cat > "${target_dir}/compose.yml" <<EOF
services:
  rackpatch-agent:
    container_name: rackpatch-agent
    image: \${RACKPATCH_AGENT_IMAGE}
EOF
  if [[ "${use_build}" == "yes" ]]; then
    cat >> "${target_dir}/compose.yml" <<'EOF'
    build:
      context: ./src
      dockerfile: Dockerfile.agent
EOF
  fi
  cat >> "${target_dir}/compose.yml" <<EOF
    restart: unless-stopped
    environment:
      RACKPATCH_SERVER_URL: ${server_url}
      RACKPATCH_AGENT_BOOTSTRAP_TOKEN: ${bootstrap_token}
      RACKPATCH_AGENT_NAME: ${name}
      RACKPATCH_AGENT_LABELS: ${labels}
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
  if [[ "${docker_socket_gid}" =~ ^[0-9]+$ ]]; then
    cat >> "${target_dir}/compose.yml" <<EOF
    group_add:
      - "${docker_socket_gid}"
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
ensure_systemd_root

if [[ "${mode}" != "compose" && "${mode}" != "container" && "${mode}" != "systemd" ]]; then
  echo "unsupported mode: ${mode}" >&2
  usage
  exit 1
fi

if [[ "${mode}" == "compose" ]]; then
  local_image="rackpatch-agent:local"
  agent_image="$(derive_agent_image || true)"
  source_build="no"
  if [[ -z "${agent_image}" ]]; then
    source_build="yes"
    agent_image="${local_image}"
  fi
  mkdir -p "${compose_dir}" "${compose_dir}/state"
  if [[ "${source_build}" == "yes" ]]; then
    src_root="$(resolve_source)"
    prepare_source_context "${src_root}" "${compose_dir}"
  fi
  write_image_env "${compose_dir}" "${agent_image}"
  write_compose_file "${compose_dir}" "compose" "./state" "${source_build}"
  if [[ "${source_build}" == "yes" ]]; then
    run_compose "${compose_dir}" up -d --build
  else
    run_compose "${compose_dir}" pull rackpatch-agent
    run_compose "${compose_dir}" up -d
  fi
  echo "compose agent installed under ${compose_dir}"
  exit 0
fi

if [[ "${mode}" == "container" ]]; then
  local_image="rackpatch-agent:local"
  agent_image="$(derive_agent_image || true)"
  source_build="no"
  if [[ -z "${agent_image}" ]]; then
    source_build="yes"
    agent_image="${local_image}"
  fi
  mkdir -p "${install_dir}"
  if [[ "${source_build}" == "yes" ]]; then
    src_root="$(resolve_source)"
    prepare_source_context "${src_root}" "${install_dir}"
  fi
  write_image_env "${install_dir}" "${agent_image}"
  write_compose_file "${install_dir}" "container" "/var/lib/rackpatch-agent" "${source_build}"
  if [[ "${source_build}" == "yes" ]]; then
    run_compose "${install_dir}" up -d --build
  else
    run_compose "${install_dir}" pull rackpatch-agent
    run_compose "${install_dir}" up -d
  fi
  echo "container agent installed under ${install_dir}"
  exit 0
fi

src_root="$(resolve_source)"
mkdir -p "${install_dir}"
rm -rf "${install_dir}/app"
cp -R "${src_root}/app" "${install_dir}/app"
cp "${src_root}/requirements-rackpatch.txt" "${install_dir}/requirements-rackpatch.txt"
ensure_python_venv() {
  local venv_dir="$1"
  local log_file
  log_file="$(mktemp)"
  if python3 -m venv "${venv_dir}" >"${log_file}" 2>&1; then
    rm -f "${log_file}"
    return 0
  fi
  if ! grep -q "ensurepip is not available" "${log_file}" || ! command -v apt-get >/dev/null 2>&1; then
    cat "${log_file}" >&2
    rm -f "${log_file}"
    return 1
  fi
  local versioned_pkg
  versioned_pkg="$(python3 - <<'PY'
import sys
print(f"python{sys.version_info.major}.{sys.version_info.minor}-venv")
PY
)"
  apt-get update
  if ! apt-get install -y "${versioned_pkg}"; then
    apt-get install -y python3-venv
  fi
  rm -rf "${venv_dir}"
  if ! python3 -m venv "${venv_dir}" >"${log_file}" 2>&1; then
    cat "${log_file}" >&2
    rm -f "${log_file}"
    return 1
  fi
  rm -f "${log_file}"
}

ensure_python_venv "${install_dir}/venv"
"${install_dir}/venv/bin/pip" install --upgrade pip
"${install_dir}/venv/bin/pip" install -r "${install_dir}/requirements-rackpatch.txt"
existing_helper_socket=""
if [[ -f "${install_dir}/env" ]]; then
  existing_helper_socket="$(awk -F= '/^RACKPATCH_HOST_HELPER_SOCKET=/{print substr($0, index($0, "=")+1)}' "${install_dir}/env" | tail -n 1)"
fi
if ! id -u "${systemd_agent_user}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/rackpatch-agent --shell /usr/sbin/nologin "${systemd_agent_user}"
fi
install -d -m 0755 /var/lib/rackpatch-agent
chown -R "${systemd_agent_user}:${systemd_agent_user}" "${install_dir}" /var/lib/rackpatch-agent
if getent group docker >/dev/null 2>&1; then
  usermod -aG docker "${systemd_agent_user}" || true
fi
cat > "${install_dir}/env" <<EOF
RACKPATCH_SERVER_URL=${server_url}
RACKPATCH_AGENT_BOOTSTRAP_TOKEN=${bootstrap_token}
RACKPATCH_AGENT_NAME=${name}
RACKPATCH_AGENT_LABELS=${labels}
RACKPATCH_AGENT_MODE=systemd
RACKPATCH_AGENT_STATE_DIR=/var/lib/rackpatch-agent
RACKPATCH_AGENT_INSTALL_DIR=${install_dir}
PYTHONPATH=${install_dir}/app
EOF
if [[ -n "${existing_helper_socket}" ]]; then
  printf 'RACKPATCH_HOST_HELPER_SOCKET=%s\n' "${existing_helper_socket}" >> "${install_dir}/env"
fi

cat > /etc/systemd/system/rackpatch-agent.service <<'EOF'
[Unit]
Description=rackpatch agent
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/opt/rackpatch-agent/env
User=rackpatch-agent
Group=rackpatch-agent
ExecStart=/opt/rackpatch-agent/venv/bin/python -m agent.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now rackpatch-agent.service
echo "systemd agent installed"
