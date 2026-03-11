#!/usr/bin/env bash

set -euo pipefail

/workspace/scripts/prepare_ssh_dir.sh /ssh-source /root/.ssh
exec python3 /opt/ops-api/server.py
