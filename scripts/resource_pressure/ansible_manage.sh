#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAYBOOK_PATH="${SCRIPT_DIR}/ansible_manage.yml"
INVENTORY_PATH="${2:-${HOSTS_FILE:-${SCRIPT_DIR}/hosts.ini}}"
ACTION="${1:-}"

if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo "缺少 ansible-playbook，请先在跳板机安装 Ansible。" >&2
  exit 1
fi

if [ -z "${ACTION}" ]; then
  cat <<EOF
用法:
  bash scripts/resource_pressure/ansible_manage.sh <start|stop|status|cleanup-disk> [inventory]

示例:
  bash scripts/resource_pressure/ansible_manage.sh start scripts/resource_pressure/hosts.ini
  MIN_PERCENT=30 MAX_PERCENT=35 CPU_GAIN=2.2 bash scripts/resource_pressure/ansible_manage.sh start
EOF
  exit 1
fi

ansible-playbook -i "${INVENTORY_PATH}" "${PLAYBOOK_PATH}" \
  -e "action=${ACTION}" \
  -e "remote_base=${REMOTE_BASE:-/tmp/resource_pressure}" \
  -e "remote_log_dir=${REMOTE_LOG_DIR:-/tmp/resource_pressure_logs}" \
  -e "remote_disk_target=${REMOTE_DISK_TARGET:-/data/tmp-disk-pressure}" \
  -e "remote_cpu_state_root=${REMOTE_CPU_STATE_ROOT:-/tmp/resource_pressure_cpu}" \
  -e "remote_memory_state_dir=${REMOTE_MEMORY_STATE_DIR:-/dev/shm/resource_pressure_memory}" \
  -e "min_percent=${MIN_PERCENT:-22}" \
  -e "max_percent=${MAX_PERCENT:-28}" \
  -e "check_interval=${CHECK_INTERVAL:-3}" \
  -e "cpu_gain=${CPU_GAIN:-1.8}" \
  -e "max_workers=${MAX_WORKERS:-}" \
  -e "chunk_mb=${CHUNK_MB:-256}"
