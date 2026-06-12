#!/usr/bin/env bash
set -euo pipefail

MIN_PERCENT="${MIN_PERCENT:-22}"
MAX_PERCENT="${MAX_PERCENT:-28}"
CHUNK_MB="${CHUNK_MB:-256}"
MIN_FREE_PERCENT="${MIN_FREE_PERCENT:-5}"
TARGET_PATH="${TARGET_PATH:-/data/tmp-disk-pressure}"
CLEANUP="${1:-}"
STATUS_FILE="${TARGET_PATH}/.status"

mkdir -p "${TARGET_PATH}"

cleanup_files() {
  rm -f "${TARGET_PATH}"/disk-pressure-*.bin
  rm -f "${STATUS_FILE}"
}

write_status() {
  local status="$1"
  local used_percent="$2"
  local message="$3"
  cat > "${STATUS_FILE}" <<EOF
status=${status}
target_path=${TARGET_PATH}
used_percent=${used_percent}
message=${message}
timestamp=$(date '+%Y-%m-%d %H:%M:%S')
EOF
}

if [ "${CLEANUP}" = "--cleanup" ]; then
  cleanup_files
  echo "已清理 ${TARGET_PATH} 中的压力文件"
  exit 0
fi

read_disk_stats() {
  df -P -B1 "${TARGET_PATH}" | awk 'NR==2 { printf "%s %s %s\n", $2, $3, $4 }'
}

write_chunk() {
  local file_path="$1"
  local size_mb="$2"
  dd if=/dev/zero of="${file_path}" bs=1M count="${size_mb}" status=none
  sync -f "${file_path}" 2>/dev/null || true
}

TARGET_PERCENT="$(awk -v min="${MIN_PERCENT}" -v max="${MAX_PERCENT}" 'BEGIN { printf "%.2f", (min + max) / 2 }')"
echo "目标目录: ${TARGET_PATH}"
echo "目标区间: ${MIN_PERCENT}% - ${MAX_PERCENT}%"
echo "目标中位值: ${TARGET_PERCENT}%"

INDEX=1
while :; do
  read -r TOTAL USED FREE <<EOF
$(read_disk_stats)
EOF
  USED_PERCENT="$(awk -v used="${USED}" -v total="${TOTAL}" 'BEGIN {
    if (total <= 0) { print "0.00"; exit }
    printf "%.2f\n", used * 100 / total
  }')"
  FREE_PERCENT="$(awk -v free="${FREE}" -v total="${TOTAL}" 'BEGIN {
    if (total <= 0) { print "0.00"; exit }
    printf "%.2f\n", free * 100 / total
  }')"

  echo "当前磁盘使用率: ${USED_PERCENT}% | 剩余空间: ${FREE_PERCENT}%"

  if awk -v free="${FREE_PERCENT}" -v min_free="${MIN_FREE_PERCENT}" 'BEGIN { exit !(free <= min_free) }'; then
    write_status "stopped" "${USED_PERCENT}" "remaining_free_below_threshold"
    echo "剩余空间已低于保护线 ${MIN_FREE_PERCENT}% ，停止写盘"
    exit 1
  fi

  if awk -v used="${USED_PERCENT}" -v min="${MIN_PERCENT}" 'BEGIN { exit !(used < min) }'; then
    target_used_bytes="$(awk -v total="${TOTAL}" -v target="${TARGET_PERCENT}" 'BEGIN { printf "%.0f\n", total * target / 100 }')"
    need_bytes=$(( target_used_bytes - USED ))
    if [ "${need_bytes}" -lt 0 ]; then
      need_bytes=0
    fi
    chunk_bytes=$(( CHUNK_MB * 1024 * 1024 ))
    if [ "${need_bytes}" -lt "${chunk_bytes}" ] && [ "${need_bytes}" -gt 0 ]; then
      write_mb="$(awk -v bytes="${need_bytes}" 'BEGIN {
        mb = int((bytes + 1024 * 1024 - 1) / (1024 * 1024));
        if (mb < 1) mb = 1;
        print mb;
      }')"
    else
      write_mb="${CHUNK_MB}"
    fi
    file_path="${TARGET_PATH}/disk-pressure-$(printf '%05d' "${INDEX}").bin"
    echo "写入文件: $(basename "${file_path}") 大小 ${write_mb} MB"
    write_chunk "${file_path}" "${write_mb}"
    write_status "writing" "${USED_PERCENT}" "writing_disk_pressure_file"
    INDEX=$((INDEX + 1))
    continue
  fi

  write_status "completed" "${USED_PERCENT}" "target_range_reached"
  echo "磁盘使用率已达到目标区间，脚本退出"
  exit 0
done
