#!/usr/bin/env bash

set -euo pipefail

source_dir="${1:-/ssh-source}"
target_dir="${2:-/root/.ssh}"

mkdir -p "${target_dir}"
chmod 700 "${target_dir}"

if [ -d "${source_dir}" ]; then
  find "${target_dir}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -a "${source_dir}/." "${target_dir}/"
  chown -R root:root "${target_dir}"
  find "${target_dir}" -type d -exec chmod 700 {} +
  find "${target_dir}" -type f -name '*.pub' -exec chmod 644 {} +
  find "${target_dir}" -type f ! -name '*.pub' -exec chmod 600 {} +
fi
