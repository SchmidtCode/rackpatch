#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  enable-agent-host-maintenance.sh --mode compose|container|systemd [--compose-dir DIR] [--install-dir DIR] [--socket-path PATH] [--helper-user USER] [--install-source PATH_OR_REPO] [--install-ref REF]
EOF
}

mode=""
compose_dir="/srv/compose/rackpatch-agent"
install_dir="/opt/rackpatch-agent"
helper_dir="/opt/rackpatch-host-helper"
socket_path="/run/rackpatch-host-helper.sock"
helper_user="rackpatch-agent"
install_source=""
install_ref=""
tmp_root=""

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
    --socket-path)
      socket_path="$2"
      shift 2
      ;;
    --helper-user)
      helper_user="$2"
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

if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

if [[ -z "${mode}" ]]; then
  usage
  exit 1
fi

if [[ "${mode}" != "compose" && "${mode}" != "container" && "${mode}" != "systemd" ]]; then
  echo "unsupported mode: ${mode}" >&2
  usage
  exit 1
fi

normalize_socket_path() {
  if [[ "${mode}" != "compose" && "${mode}" != "container" ]]; then
    return
  fi
  local socket_dir
  socket_dir="$(dirname "${socket_path}")"
  if [[ "${socket_dir}" == "/run" ]]; then
    socket_path="/run/rackpatch-host-helper/$(basename "${socket_path}")"
  fi
}

if [[ -z "${install_source}" ]]; then
  if [[ -n "${BASH_SOURCE[0]-}" ]]; then
    install_source="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  else
    install_source="$(pwd)"
  fi
fi

cleanup() {
  if [[ -n "${tmp_root}" && -d "${tmp_root}" ]]; then
    rm -rf "${tmp_root}"
  fi
}
trap cleanup EXIT

resolve_source() {
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

src_root="$(resolve_source)"

create_helper_user() {
  if ! id -u "${helper_user}" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "${helper_dir}" --shell /usr/sbin/nologin "${helper_user}"
  fi
}

install_helper_files() {
  install -d -m 0755 "${helper_dir}" /usr/local/libexec
  install -m 0755 "${src_root}/scripts/host-maintenance/helper_server.py" "${helper_dir}/helper_server.py"
  install -m 0755 "${src_root}/scripts/host-maintenance/package_check.py" /usr/local/libexec/rackpatch-package-check
  install -m 0755 "${src_root}/scripts/host-maintenance/package_patch.py" /usr/local/libexec/rackpatch-package-patch
  chown -R root:root "${helper_dir}" /usr/local/libexec/rackpatch-package-check /usr/local/libexec/rackpatch-package-patch
}

write_helper_env() {
  cat > /etc/default/rackpatch-host-helper <<EOF
RACKPATCH_HOST_HELPER_SOCKET=${socket_path}
RACKPATCH_HOST_PACKAGE_CHECK_CMD=/usr/local/libexec/rackpatch-package-check
RACKPATCH_HOST_PACKAGE_PATCH_CMD=/usr/local/libexec/rackpatch-package-patch
RACKPATCH_HOST_HELPER_SOCKET_MODE=660
EOF
}

write_sudoers() {
  cat > /etc/sudoers.d/rackpatch-agent-maintenance <<EOF
User_Alias RACKPATCH_HELPER = ${helper_user}
Cmnd_Alias RACKPATCH_HELPER_CMDS = /usr/local/libexec/rackpatch-package-check, /usr/local/libexec/rackpatch-package-patch

Defaults:RACKPATCH_HELPER !requiretty
Defaults!RACKPATCH_HELPER_CMDS env_reset,secure_path=/usr/sbin:/usr/bin:/sbin:/bin

RACKPATCH_HELPER ALL=(root) NOPASSWD: RACKPATCH_HELPER_CMDS
EOF
  chmod 0440 /etc/sudoers.d/rackpatch-agent-maintenance
  if command -v visudo >/dev/null 2>&1; then
    visudo -cf /etc/sudoers.d/rackpatch-agent-maintenance >/dev/null
  fi
}

write_helper_service() {
  cat > /etc/systemd/system/rackpatch-host-helper.service <<EOF
[Unit]
Description=rackpatch host maintenance helper
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/default/rackpatch-host-helper
User=${helper_user}
Group=${helper_user}
ExecStart=/usr/bin/python3 ${helper_dir}/helper_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

restart_helper_service() {
  systemctl daemon-reload
  systemctl enable --now rackpatch-host-helper.service
  systemctl restart rackpatch-host-helper.service
}

write_compose_override() {
  local target_dir="$1"
  local socket_dir
  socket_dir="$(dirname "${socket_path}")"
  cat > "${target_dir}/compose.host-maintenance.yml" <<EOF
services:
  rackpatch-agent:
    environment:
      RACKPATCH_HOST_HELPER_SOCKET: ${socket_path}
    volumes:
      - ${socket_dir}:${socket_dir}
EOF
}

restart_compose_agent() {
  local target_dir="$1"
  local build_flag="$2"
  local -a files=(-f "${target_dir}/compose.yml")
  if [[ -f "${target_dir}/compose.host-maintenance.yml" ]]; then
    files+=(-f "${target_dir}/compose.host-maintenance.yml")
  fi
  docker compose "${files[@]}" up -d ${build_flag}
}

configure_systemd_agent() {
  local env_file="${install_dir}/env"
  if [[ ! -f "${env_file}" ]]; then
    echo "missing systemd agent env file: ${env_file}" >&2
    exit 1
  fi
  if grep -q '^RACKPATCH_HOST_HELPER_SOCKET=' "${env_file}"; then
    sed -i "s|^RACKPATCH_HOST_HELPER_SOCKET=.*|RACKPATCH_HOST_HELPER_SOCKET=${socket_path}|" "${env_file}"
  else
    printf '\nRACKPATCH_HOST_HELPER_SOCKET=%s\n' "${socket_path}" >> "${env_file}"
  fi
  systemctl restart rackpatch-agent.service
}

normalize_socket_path
create_helper_user
install_helper_files
write_helper_env
write_sudoers
write_helper_service
restart_helper_service

case "${mode}" in
  compose)
    write_compose_override "${compose_dir}"
    restart_compose_agent "${compose_dir}" "--build"
    ;;
  container)
    write_compose_override "${install_dir}"
    restart_compose_agent "${install_dir}" ""
    ;;
  systemd)
    configure_systemd_agent
    ;;
esac

echo "host maintenance helper enabled for ${mode} mode"
