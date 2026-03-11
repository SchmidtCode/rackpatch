#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install-agent.sh --server-url URL --bootstrap-token TOKEN --mode container|systemd [--name NAME] [--labels a,b] [--install-source PATH]
EOF
}

server_url=""
bootstrap_token=""
mode="container"
name="$(hostname)"
labels=""
install_source="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_root=""

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
    git clone --depth 1 "${install_source}" "${tmp_root}" >/dev/null 2>&1
    printf '%s\n' "${tmp_root}"
    return
  fi
  echo "install source not found: ${install_source}" >&2
  exit 1
}

src_root="$(resolve_source)"

if [[ "${mode}" == "container" ]]; then
  install_dir="/opt/rackpatch-agent"
  mkdir -p "${install_dir}"
  rm -rf "${install_dir}/src"
  mkdir -p "${install_dir}/src"
  cp -R "${src_root}/app" "${install_dir}/src/app"
  cp "${src_root}/Dockerfile.agent" "${install_dir}/src/Dockerfile.agent"
  cp "${src_root}/requirements-ops.txt" "${install_dir}/src/requirements-ops.txt"
  docker build -t rackpatch-agent:local -f "${install_dir}/src/Dockerfile.agent" "${install_dir}/src"
  cat > "${install_dir}/compose.yml" <<EOF
services:
  rackpatch-agent:
    image: rackpatch-agent:local
    restart: unless-stopped
    environment:
      OPS_SERVER_URL: ${server_url}
      OPS_AGENT_BOOTSTRAP_TOKEN: ${bootstrap_token}
      OPS_AGENT_NAME: ${name}
      OPS_AGENT_LABELS: ${labels}
      OPS_AGENT_MODE: container
    volumes:
      - /var/lib/rackpatch-agent:/var/lib/ops-agent
      - /var/run/docker.sock:/var/run/docker.sock
EOF
  docker compose -f "${install_dir}/compose.yml" up -d
  echo "container agent installed under ${install_dir}"
  exit 0
fi

install_dir="/opt/rackpatch-agent"
mkdir -p "${install_dir}"
rm -rf "${install_dir}/app"
cp -R "${src_root}/app" "${install_dir}/app"
cp "${src_root}/requirements-ops.txt" "${install_dir}/requirements-ops.txt"
python3 -m venv "${install_dir}/venv"
"${install_dir}/venv/bin/pip" install --upgrade pip
"${install_dir}/venv/bin/pip" install -r "${install_dir}/requirements-ops.txt"
cat > "${install_dir}/env" <<EOF
OPS_SERVER_URL=${server_url}
OPS_AGENT_BOOTSTRAP_TOKEN=${bootstrap_token}
OPS_AGENT_NAME=${name}
OPS_AGENT_LABELS=${labels}
OPS_AGENT_MODE=systemd
OPS_AGENT_STATE_DIR=/var/lib/rackpatch-agent
PYTHONPATH=${install_dir}/app
EOF

cat > /etc/systemd/system/rackpatch-agent.service <<'EOF'
[Unit]
Description=rackpatch agent
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/opt/rackpatch-agent/env
ExecStart=/opt/rackpatch-agent/venv/bin/python -m agent.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now rackpatch-agent.service
echo "systemd agent installed"
