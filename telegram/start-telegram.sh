#!/usr/bin/env bash

set -euo pipefail

/usr/local/bin/prepare_ssh_dir.sh /ssh-source /root/.ssh
exec python3 /opt/ops-telegram/bot.py
