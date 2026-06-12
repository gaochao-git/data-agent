#!/usr/bin/env bash
set -euo pipefail

MIN_PERCENT="${MIN_PERCENT:-22}"
MAX_PERCENT="${MAX_PERCENT:-28}"
CHECK_INTERVAL="${CHECK_INTERVAL:-3}"
CPU_GAIN="${CPU_GAIN:-1.8}"
STATE_ROOT="${STATE_ROOT:-/tmp/resource_pressure_cpu}"
SCRIPT_PID="$$"
STATE_DIR="${STATE_ROOT}/${SCRIPT_PID}"
WORKER_DIR="${STATE_DIR}/workers"

mkdir -p "${WORKER_DIR}"

cleanup() {
  if [ -d "${WORKER_DIR}" ]; then
    find "${WORKER_DIR}" -name '*.pid' -type f | while read -r pid_file; do
      worker_pid="$(cat "${pid_file}" 2>/dev/null || true)"
      if [ -n "${worker_pid}" ] && kill -0 "${worker_pid}" 2>/dev/null; then
        kill "${worker_pid}" 2>/dev/null || true
      fi
    done
  fi
  rm -rf "${STATE_DIR}"
}

trap cleanup EXIT INT TERM

read_cpu_usage() {
  local total1 idle1 total2 idle2
  read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
  total1=$((user + nice + system + idle + iowait + irq + softirq + steal))
  idle1=$((idle + iowait))
  sleep 1
  read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
  total2=$((user + nice + system + idle + iowait + irq + softirq + steal))
  idle2=$((idle + iowait))
  awk -v total1="${total1}" -v total2="${total2}" -v idle1="${idle1}" -v idle2="${idle2}" 'BEGIN {
    total = total2 - total1;
    idle = idle2 - idle1;
    if (total <= 0) {
      print "0.00";
      exit;
    }
    used = (1 - idle / total) * 100;
    if (used < 0) used = 0;
    printf "%.2f\n", used;
  }'
}

launch_full_worker() {
  local slot="$1"
  (
    while :; do :; done
  ) &
  echo "$!" > "${WORKER_DIR}/full_${slot}.pid"
}

launch_partial_worker() {
  local duty_percent="$1"
  (
    while :; do
      end_ms=$(( $(date +%s%3N) + duty_percent * 2 ))
      while [ "$(date +%s%3N)" -lt "${end_ms}" ]; do :; done
      sleep 0.$(( (100 - duty_percent) * 2 ))
    done
  ) &
  echo "$!" > "${WORKER_DIR}/partial.pid"
}

stop_worker_file() {
  local pid_file="$1"
  if [ ! -f "${pid_file}" ]; then
    return
  fi
  local worker_pid=""
  worker_pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [ -n "${worker_pid}" ] && kill -0 "${worker_pid}" 2>/dev/null; then
    kill "${worker_pid}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
}

count_running_workers() {
  find "${WORKER_DIR}" -name '*.pid' -type f | wc -l | awk '{print $1}'
}

ensure_worker_count() {
  local desired_full="$1"
  local partial_duty="$2"
  local slot=1
  while [ "${slot}" -le "${MAX_WORKERS}" ]; do
    pid_file="${WORKER_DIR}/full_${slot}.pid"
    if [ "${slot}" -le "${desired_full}" ]; then
      if [ ! -f "${pid_file}" ]; then
        launch_full_worker "${slot}"
      fi
    else
      stop_worker_file "${pid_file}"
    fi
    slot=$((slot + 1))
  done

  if [ "${partial_duty}" -gt 0 ]; then
    stop_worker_file "${WORKER_DIR}/partial.pid"
    launch_partial_worker "${partial_duty}"
  else
    stop_worker_file "${WORKER_DIR}/partial.pid"
  fi
}

CPU_COUNT="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
MAX_WORKERS="${MAX_WORKERS:-${CPU_COUNT}}"
TARGET_PERCENT="$(awk -v min="${MIN_PERCENT}" -v max="${MAX_PERCENT}" 'BEGIN { printf "%.2f", (min + max) / 2 }')"

echo "CPU 核心数: ${CPU_COUNT}"
echo "目标区间: ${MIN_PERCENT}% - ${MAX_PERCENT}%"
echo "目标中位值: ${TARGET_PERCENT}%"
echo "最大 worker 数: ${MAX_WORKERS}"
echo "反馈增益: ${CPU_GAIN}"

while :; do
  CURRENT_PERCENT="$(read_cpu_usage)"
  CURRENT_WORKERS="$(count_running_workers)"
  PLAN="$(awk -v cpu="${CPU_COUNT}" -v cur="${CURRENT_PERCENT}" -v min="${MIN_PERCENT}" -v max="${MAX_PERCENT}" -v target="${TARGET_PERCENT}" -v running="${CURRENT_WORKERS}" -v gain="${CPU_GAIN}" -v max_workers="${MAX_WORKERS}" 'BEGIN {
    error = target - cur;
    desired = running + cpu * error / 100 * gain;
    if (cur >= min && cur <= max) desired = running;
    if (desired < 0) desired = 0;
    if (desired > max_workers) desired = max_workers;
    full = int(desired);
    remainder = desired - full;
    partial = int(remainder * 100 + 0.5);
    printf "%d %d %.2f\n", full, partial, error;
  }')"
  DESIRED_FULL="$(echo "${PLAN}" | awk '{print $1}')"
  PARTIAL_DUTY="$(echo "${PLAN}" | awk '{print $2}')"
  ERROR_PERCENT="$(echo "${PLAN}" | awk '{print $3}')"
  ensure_worker_count "${DESIRED_FULL}" "${PARTIAL_DUTY}"
  RUNNING_WORKERS="$(count_running_workers)"
  echo "当前 CPU 使用率: ${CURRENT_PERCENT}% | 偏差: ${ERROR_PERCENT}% | worker 数: ${RUNNING_WORKERS} | 目标 full=${DESIRED_FULL} partial=${PARTIAL_DUTY}"
  sleep "${CHECK_INTERVAL}"
done
