#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  enable-agent-host-maintenance.sh --mode compose|container|systemd [--compose-dir DIR] [--install-dir DIR] [--socket-path PATH] [--helper-user USER] [--preset packages|proxmox|all] [--allow-actions action1,action2] [--install-source PATH_OR_REPO] [--install-ref REF]
EOF
}

mode=""
compose_dir="/srv/compose/rackpatch-agent"
install_dir="/opt/rackpatch-agent"
helper_dir="/opt/rackpatch-host-helper"
socket_path="/run/rackpatch-host-helper/rackpatch-host-helper.sock"
helper_user="rackpatch-agent"
preset="packages"
allow_actions=""
install_source=""
install_ref=""
tmp_root=""
selected_actions=()
compose_file=""
compose_service=""
compose_profile=""
compose_override_file=""

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
    --preset)
      preset="$2"
      shift 2
      ;;
    --allow-actions)
      allow_actions="$2"
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
  local socket_dir
  socket_dir="$(dirname "${socket_path}")"
  if [[ "${socket_dir}" == "/run" ]]; then
    socket_path="/run/rackpatch-host-helper/$(basename "${socket_path}")"
  fi
}

socket_dir_path() {
  dirname "${socket_path}"
}

socket_group_id() {
  local path="${1:-}"
  if [[ -z "${path}" || ! -e "${path}" ]]; then
    return 0
  fi
  stat -c '%g' "${path}" 2>/dev/null || true
}

resolve_actions() {
  local raw_actions=""
  case "${preset}" in
    packages)
      raw_actions="package_check,package_patch"
      ;;
    proxmox)
      raw_actions="proxmox_patch,proxmox_reboot"
      ;;
    all|packages+proxmox)
      raw_actions="package_check,package_patch,proxmox_patch,proxmox_reboot"
      ;;
    *)
      echo "unsupported preset: ${preset}" >&2
      exit 1
      ;;
  esac
  if [[ -n "${allow_actions}" ]]; then
    raw_actions="${allow_actions}"
  fi

  local item action
  local -A seen=()
  IFS=',' read -r -a requested_actions <<< "${raw_actions}"
  selected_actions=()
  for item in "${requested_actions[@]}"; do
    action="${item//[[:space:]]/}"
    if [[ -z "${action}" ]]; then
      continue
    fi
    case "${action}" in
      package_check|package_patch|proxmox_patch|proxmox_reboot)
        ;;
      *)
        echo "unsupported host-maintenance action: ${action}" >&2
        exit 1
        ;;
    esac
    if [[ -n "${seen[$action]+x}" ]]; then
      continue
    fi
    seen["${action}"]=1
    selected_actions+=("${action}")
  done
  if [[ ${#selected_actions[@]} -eq 0 ]]; then
    echo "no host-maintenance actions selected" >&2
    exit 1
  fi
}

selected_actions_csv() {
  local IFS=,
  printf '%s' "${selected_actions[*]}"
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
  install -m 0755 "${src_root}/scripts/host-maintenance/proxmox_patch.py" /usr/local/libexec/rackpatch-proxmox-patch
  install -m 0755 "${src_root}/scripts/host-maintenance/proxmox_reboot.py" /usr/local/libexec/rackpatch-proxmox-reboot
  chown -R root:root \
    "${helper_dir}" \
    /usr/local/libexec/rackpatch-package-check \
    /usr/local/libexec/rackpatch-package-patch \
    /usr/local/libexec/rackpatch-proxmox-patch \
    /usr/local/libexec/rackpatch-proxmox-reboot
}

write_helper_env() {
  cat > /etc/default/rackpatch-host-helper <<EOF
RACKPATCH_HOST_HELPER_SOCKET=${socket_path}
RACKPATCH_HOST_HELPER_ACTIONS=$(selected_actions_csv)
RACKPATCH_HOST_PACKAGE_CHECK_CMD=/usr/local/libexec/rackpatch-package-check
RACKPATCH_HOST_PACKAGE_PATCH_CMD=/usr/local/libexec/rackpatch-package-patch
RACKPATCH_HOST_PROXMOX_PATCH_CMD=/usr/local/libexec/rackpatch-proxmox-patch
RACKPATCH_HOST_PROXMOX_REBOOT_CMD=/usr/local/libexec/rackpatch-proxmox-reboot
RACKPATCH_HOST_HELPER_SOCKET_MODE=660
EOF
}

write_sudoers() {
  local commands=()
  local action command_csv
  for action in "${selected_actions[@]}"; do
    case "${action}" in
      package_check)
        commands+=("/usr/local/libexec/rackpatch-package-check")
        ;;
      package_patch)
        commands+=("/usr/local/libexec/rackpatch-package-patch")
        ;;
      proxmox_patch)
        commands+=("/usr/local/libexec/rackpatch-proxmox-patch")
        ;;
      proxmox_reboot)
        commands+=("/usr/local/libexec/rackpatch-proxmox-reboot")
        ;;
    esac
  done
  local IFS=,
  command_csv="${commands[*]}"
  cat > /etc/sudoers.d/rackpatch-agent-maintenance <<EOF
User_Alias RACKPATCH_HELPER = ${helper_user}
Cmnd_Alias RACKPATCH_HELPER_CMDS = ${command_csv}

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
  local socket_dir runtime_dir_block=""
  socket_dir="$(socket_dir_path)"
  if [[ "${socket_dir}" == /run/* && "${socket_dir}" != "/run" ]]; then
    runtime_dir_block=$(cat <<EOF
RuntimeDirectory=${socket_dir#/run/}
RuntimeDirectoryMode=0755
EOF
)
  fi
  cat > /etc/systemd/system/rackpatch-host-helper.service <<EOF
[Unit]
Description=rackpatch host maintenance helper
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/default/rackpatch-host-helper
User=${helper_user}
Group=${helper_user}
${runtime_dir_block}
ExecStart=/usr/bin/python3 ${helper_dir}/helper_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

prepare_socket_directory() {
  local socket_dir
  socket_dir="$(socket_dir_path)"
  if [[ "${socket_dir}" == "/run" ]]; then
    return
  fi
  install -d -m 0755 -o "${helper_user}" -g "${helper_user}" "${socket_dir}"
}

restart_helper_service() {
  systemctl daemon-reload
  systemctl enable --now rackpatch-host-helper.service
  systemctl restart rackpatch-host-helper.service
}

detect_compose_target() {
  local target_dir="$1"
  local candidate=""
  for candidate in docker-compose.yml compose.yml; do
    if [[ -f "${target_dir}/${candidate}" ]]; then
      compose_file="${target_dir}/${candidate}"
      break
    fi
  done
  if [[ -z "${compose_file}" ]]; then
    echo "missing compose file in ${target_dir}" >&2
    exit 1
  fi
  if grep -q '^[[:space:]]\{2\}rackpatch-agent:' "${compose_file}"; then
    compose_service="rackpatch-agent"
    compose_override_file="${target_dir}/compose.host-maintenance.yml"
    return
  fi
  if grep -q '^[[:space:]]\{2\}agent:' "${compose_file}"; then
    compose_service="agent"
    compose_profile="self-agent"
    compose_override_file=""
    return
  fi
  echo "could not find an agent service in ${compose_file}" >&2
  exit 1
}

write_compose_override() {
  local socket_dir helper_socket_gid docker_socket_gid
  socket_dir="$(dirname "${socket_path}")"
  helper_socket_gid="$(socket_group_id "${socket_path}")"
  docker_socket_gid="$(socket_group_id /var/run/docker.sock)"
  if [[ -z "${compose_override_file}" ]]; then
    return
  fi
  cat > "${compose_override_file}" <<EOF
services:
  ${compose_service}:
    environment:
      RACKPATCH_HOST_HELPER_SOCKET: ${socket_path}
    volumes:
      - ${socket_dir}:${socket_dir}
EOF
  if [[ "${docker_socket_gid}" =~ ^[0-9]+$ || "${helper_socket_gid}" =~ ^[0-9]+$ ]]; then
    cat >> "${compose_override_file}" <<EOF
    group_add:
EOF
    if [[ "${docker_socket_gid}" =~ ^[0-9]+$ ]]; then
      cat >> "${compose_override_file}" <<EOF
      - "${docker_socket_gid}"
EOF
    fi
    if [[ "${helper_socket_gid}" =~ ^[0-9]+$ && "${helper_socket_gid}" != "${docker_socket_gid}" ]]; then
      cat >> "${compose_override_file}" <<EOF
      - "${helper_socket_gid}"
EOF
    fi
  fi
}

restart_compose_agent() {
  local target_dir="$1"
  detect_compose_target "${target_dir}"
  write_compose_override
  local -a files=(-f "${compose_file}")
  local -a compose_options=()
  if [[ -f "${target_dir}/agent.env" && "$(basename "${compose_file}")" == "compose.yml" ]]; then
    files=(--env-file "${target_dir}/agent.env" "${files[@]}")
  fi
  if [[ -n "${compose_override_file}" && -f "${compose_override_file}" ]]; then
    files+=(-f "${compose_override_file}")
  fi
  if [[ -n "${compose_profile}" ]]; then
    compose_options+=(--profile "${compose_profile}")
  fi
  if grep -q '^[[:space:]]*build:' "${compose_file}"; then
    docker compose "${files[@]}" "${compose_options[@]}" up -d --build "${compose_service}"
  else
    docker compose "${files[@]}" "${compose_options[@]}" up -d "${compose_service}"
  fi
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
resolve_actions
create_helper_user
install_helper_files
write_helper_env
write_sudoers
write_helper_service
prepare_socket_directory
restart_helper_service

case "${mode}" in
  compose)
    restart_compose_agent "${compose_dir}"
    ;;
  container)
    restart_compose_agent "${install_dir}"
    ;;
  systemd)
    configure_systemd_agent
    ;;
esac

echo "host maintenance helper enabled for ${mode} mode (actions: $(selected_actions_csv))"
