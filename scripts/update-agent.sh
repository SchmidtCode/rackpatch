#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  update-agent.sh --mode compose|container|systemd [--compose-dir DIR] [--install-dir DIR] [--install-source PATH_OR_REPO] [--install-ref REF]
EOF
}

mode=""
compose_dir="/srv/compose/rackpatch-agent"
install_dir="/opt/rackpatch-agent"
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

resolve_source() {
  if [[ -z "${install_source}" ]]; then
    echo "install source is required for updates" >&2
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

src_root="$(resolve_source)"
compose_override_name="compose.host-maintenance.yml"

case "${mode}" in
  compose)
    mkdir -p "${compose_dir}/src"
    rm -rf "${compose_dir}/src/app"
    cp -R "${src_root}/app" "${compose_dir}/src/app"
    cp "${src_root}/Dockerfile.agent" "${compose_dir}/src/Dockerfile.agent"
    cp "${src_root}/requirements-rackpatch.txt" "${compose_dir}/src/requirements-rackpatch.txt"
    compose_args=(-f "${compose_dir}/compose.yml")
    if [[ -f "${compose_dir}/${compose_override_name}" ]]; then
      compose_args+=(-f "${compose_dir}/${compose_override_name}")
    fi
    docker compose "${compose_args[@]}" up -d --build
    echo "rackpatch agent updated in compose mode under ${compose_dir}"
    ;;
  container)
    mkdir -p "${install_dir}/src"
    rm -rf "${install_dir}/src/app"
    cp -R "${src_root}/app" "${install_dir}/src/app"
    cp "${src_root}/Dockerfile.agent" "${install_dir}/src/Dockerfile.agent"
    cp "${src_root}/requirements-rackpatch.txt" "${install_dir}/src/requirements-rackpatch.txt"
    docker build -t rackpatch-agent:local -f "${install_dir}/src/Dockerfile.agent" "${install_dir}/src"
    compose_args=(-f "${install_dir}/compose.yml")
    if [[ -f "${install_dir}/${compose_override_name}" ]]; then
      compose_args+=(-f "${install_dir}/${compose_override_name}")
    fi
    docker compose "${compose_args[@]}" up -d
    echo "rackpatch agent updated in container mode under ${install_dir}"
    ;;
  systemd)
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
