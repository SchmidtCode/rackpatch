#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install-agent.sh --server-url URL --bootstrap-token TOKEN --mode compose|container|systemd [--compose-dir DIR] [--name NAME] [--labels a,b] [--install-source PATH] [--install-ref REF]
EOF
}

server_url=""
bootstrap_token=""
mode="container"
compose_dir="/srv/compose/rackpatch-agent"
name="$(hostname)"
labels=""
install_source=""
install_ref=""
tmp_root=""
systemd_agent_user="rackpatch-agent"

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
    --name)
      name="$2"
      shift 2
      ;;
    --labels)
      labels="$2"
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
compose_override_name="compose.host-maintenance.yml"

if [[ "${mode}" != "compose" && "${mode}" != "container" && "${mode}" != "systemd" ]]; then
  echo "unsupported mode: ${mode}" >&2
  usage
  exit 1
fi

  if [[ "${mode}" == "compose" ]]; then
  install_dir="${compose_dir}"
  mkdir -p "${install_dir}" "${install_dir}/state"
  rm -rf "${install_dir}/src"
  mkdir -p "${install_dir}/src"
  cp -R "${src_root}/app" "${install_dir}/src/app"
  cp "${src_root}/Dockerfile.agent" "${install_dir}/src/Dockerfile.agent"
  cp "${src_root}/requirements-rackpatch.txt" "${install_dir}/src/requirements-rackpatch.txt"
  cat > "${install_dir}/compose.yml" <<EOF
services:
  rackpatch-agent:
    container_name: rackpatch-agent
    image: rackpatch-agent:local
    build:
      context: ./src
      dockerfile: Dockerfile.agent
    restart: unless-stopped
    environment:
      RACKPATCH_SERVER_URL: ${server_url}
      RACKPATCH_AGENT_BOOTSTRAP_TOKEN: ${bootstrap_token}
      RACKPATCH_AGENT_NAME: ${name}
      RACKPATCH_AGENT_LABELS: ${labels}
      RACKPATCH_AGENT_MODE: compose
      RACKPATCH_AGENT_COMPOSE_DIR: ${install_dir}
      RACKPATCH_AGENT_STATE_DIR: /var/lib/rackpatch-agent
    volumes:
      - ./state:/var/lib/rackpatch-agent
      - /var/run/docker.sock:/var/run/docker.sock
EOF
  compose_args=(-f "${install_dir}/compose.yml")
  if [[ -f "${install_dir}/${compose_override_name}" ]]; then
    compose_args+=(-f "${install_dir}/${compose_override_name}")
  fi
  docker compose "${compose_args[@]}" up -d --build
  echo "compose agent installed under ${install_dir}"
  exit 0
fi

if [[ "${mode}" == "container" ]]; then
  install_dir="/opt/rackpatch-agent"
  mkdir -p "${install_dir}"
  rm -rf "${install_dir}/src"
  mkdir -p "${install_dir}/src"
  cp -R "${src_root}/app" "${install_dir}/src/app"
  cp "${src_root}/Dockerfile.agent" "${install_dir}/src/Dockerfile.agent"
  cp "${src_root}/requirements-rackpatch.txt" "${install_dir}/src/requirements-rackpatch.txt"
  docker build -t rackpatch-agent:local -f "${install_dir}/src/Dockerfile.agent" "${install_dir}/src"
  cat > "${install_dir}/compose.yml" <<EOF
services:
  rackpatch-agent:
    image: rackpatch-agent:local
    restart: unless-stopped
    environment:
      RACKPATCH_SERVER_URL: ${server_url}
      RACKPATCH_AGENT_BOOTSTRAP_TOKEN: ${bootstrap_token}
      RACKPATCH_AGENT_NAME: ${name}
      RACKPATCH_AGENT_LABELS: ${labels}
      RACKPATCH_AGENT_MODE: container
      RACKPATCH_AGENT_INSTALL_DIR: ${install_dir}
      RACKPATCH_AGENT_STATE_DIR: /var/lib/rackpatch-agent
    volumes:
      - /var/lib/rackpatch-agent:/var/lib/rackpatch-agent
      - /var/run/docker.sock:/var/run/docker.sock
EOF
  compose_args=(-f "${install_dir}/compose.yml")
  if [[ -f "${install_dir}/${compose_override_name}" ]]; then
    compose_args+=(-f "${install_dir}/${compose_override_name}")
  fi
  docker compose "${compose_args[@]}" up -d
  echo "container agent installed under ${install_dir}"
  exit 0
fi

install_dir="/opt/rackpatch-agent"
mkdir -p "${install_dir}"
rm -rf "${install_dir}/app"
cp -R "${src_root}/app" "${install_dir}/app"
cp "${src_root}/requirements-rackpatch.txt" "${install_dir}/requirements-rackpatch.txt"
python3 -m venv "${install_dir}/venv"
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
