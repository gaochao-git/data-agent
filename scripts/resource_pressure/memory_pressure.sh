#!/usr/bin/env bash
set -euo pipefail

MIN_PERCENT="${MIN_PERCENT:-22}"
MAX_PERCENT="${MAX_PERCENT:-28}"
CHECK_INTERVAL="${CHECK_INTERVAL:-5}"
CHUNK_MB="${CHUNK_MB:-128}"
STATE_DIR="${STATE_DIR:-/dev/shm/resource_pressure_memory}"

mkdir -p "${STATE_DIR}"

cleanup() {
  rm -f "${STATE_DIR}"/mem-pressure-*.bin
}

trap cleanup EXIT INT TERM

read_mem_stats() {
  awk '
    /^MemTotal:/ { total = $2 * 1024 }
    /^MemAvailable:/ { available = $2 * 1024 }
    END {
      used = total - available;
      if (used < 0) used = 0;
      printf "%d %d\n", total, used;
    }
  ' /proc/meminfo
}

ensure_chunk_file() {
  local path="$1"
  local size_mb="$2"
  dd if=/dev/zero of="${path}" bs=1M count="${size_mb}" status=none
  sync -f "${path}" 2>/dev/null || true
}

release_latest_chunk() {
  latest_file="$(ls -1 "${STATE_DIR}"/mem-pressure-*.bin 2>/dev/null | sort | tail -n 1 || true)"
  if [ -n "${latest_file}" ]; then
    rm -f "${latest_file}"
  fi
}

count_chunk_files() {
  find "${STATE_DIR}" -maxdepth 1 -type f -name 'mem-pressure-*.bin' | wc -l | awk '{print $1}'
}

TARGET_PERCENT="$(awk -v min="${MIN_PERCENT}" -v max="${MAX_PERCENT}" 'BEGIN { printf "%.2f", (min + max) / 2 }')"
echo "目标区间: ${MIN_PERCENT}% - ${MAX_PERCENT}%"
echo "目标中位值: ${TARGET_PERCENT}%"
echo "内存占用目录: ${STATE_DIR}"

INDEX=1
while :; do
  read -r TOTAL_BYTES USED_BYTES <<EOF
$(read_mem_stats)
EOF
  USED_PERCENT="$(awk -v used="${USED_BYTES}" -v total="${TOTAL_BYTES}" 'BEGIN {
    if (total <= 0) { print "0.00"; exit }
    printf "%.2f\n", used * 100 / total
  }')"
  CHUNK_COUNT="$(count_chunk_files)"

  if awk -v current="${USED_PERCENT}" -v min="${MIN_PERCENT}" 'BEGIN { exit !(current < min) }'; then
    file_path="${STATE_DIR}/mem-pressure-$(printf '%05d' "${INDEX}").bin"
    echo "当前内存使用率: ${USED_PERCENT}% | 新增 ${CHUNK_MB} MB | 文件: $(basename "${file_path}")"
    ensure_chunk_file "${file_path}" "${CHUNK_MB}"
    INDEX=$((INDEX + 1))
  elif awk -v current="${USED_PERCENT}" -v max="${MAX_PERCENT}" 'BEGIN { exit !(current > max) }'; then
    if [ "${CHUNK_COUNT}" -gt 0 ]; then
      echo "当前内存使用率: ${USED_PERCENT}% | 释放 1 个 ${CHUNK_MB} MB 块"
      release_latest_chunk
    else
      echo "当前内存使用率: ${USED_PERCENT}% | 没有可释放块，保持不变"
    fi
  else
    echo "当前内存使用率: ${USED_PERCENT}% | 保持不变 | 当前块数: ${CHUNK_COUNT}"
  fi

  sleep "${CHECK_INTERVAL}"
done
