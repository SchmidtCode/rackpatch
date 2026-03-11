#!/usr/bin/env bash

set -euo pipefail

/usr/local/bin/prepare_ssh_dir.sh /ssh-source /root/.ssh
exec /usr/local/bin/server-wrapper
