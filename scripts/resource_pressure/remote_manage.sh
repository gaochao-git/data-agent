#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOSTS_FILE="${HOSTS_FILE:-${SCRIPT_DIR}/hosts.txt}"
REMOTE_BASE="${REMOTE_BASE:-/tmp/resource_pressure}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-/tmp/resource_pressure_logs}"
REMOTE_DISK_TARGET="${REMOTE_DISK_TARGET:-/data/tmp-disk-pressure}"
REMOTE_CPU_STATE_ROOT="${REMOTE_CPU_STATE_ROOT:-/tmp/resource_pressure_cpu}"
REMOTE_MEMORY_STATE_DIR="${REMOTE_MEMORY_STATE_DIR:-/dev/shm/resource_pressure_memory}"
SSH_OPTS="${SSH_OPTS:--o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=8}"
MIN_PERCENT="${MIN_PERCENT:-22}"
MAX_PERCENT="${MAX_PERCENT:-28}"

usage() {
  cat <<EOF
用法:
  bash scripts/resource_pressure/remote_manage.sh <command> [hosts_file]

命令:
  start         下发脚本并启动远端 cpu/memory/disk 压力脚本
  status        查看远端脚本运行状态
  stop          停止远端 cpu/memory/disk 压力脚本
  cleanup-disk  清理远端 disk_pressure.sh 生成的磁盘压力文件
  sync          仅同步脚本到远端，不启动

  环境变量:
  HOSTS_FILE          主机清单文件，默认 scripts/resource_pressure/hosts.txt
  REMOTE_BASE         远端脚本目录，默认 /tmp/resource_pressure
  REMOTE_LOG_DIR      远端日志目录，默认 /tmp/resource_pressure_logs
  REMOTE_DISK_TARGET  远端磁盘压力目录，默认 /data/tmp-disk-pressure
  REMOTE_CPU_STATE_ROOT  远端 CPU 压力状态目录，默认 /tmp/resource_pressure_cpu
  REMOTE_MEMORY_STATE_DIR  远端内存压力目录，默认 /dev/shm/resource_pressure_memory
  SSH_OPTS            透传给 ssh/scp 的参数
  MIN_PERCENT         达标下限，默认 22
  MAX_PERCENT         达标上限，默认 28
EOF
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

ensure_dependencies() {
  for cmd in ssh scp; do
    if ! command_exists "${cmd}"; then
      echo "缺少依赖命令: ${cmd}" >&2
      exit 1
    fi
  done
}

iter_hosts() {
  if [ ! -f "${HOSTS_FILE}" ]; then
    echo "主机清单不存在: ${HOSTS_FILE}" >&2
    exit 1
  fi
  awk '
    {
      gsub(/\r/, "", $0)
      if ($0 ~ /^[[:space:]]*#/ || $0 ~ /^[[:space:]]*$/) next
      print $0
    }
  ' "${HOSTS_FILE}"
}

remote_exec() {
  local host="$1"
  shift
  ssh ${SSH_OPTS} -n "${host}" "$@"
}

load_hosts() {
  HOSTS=()
  while IFS= read -r host; do
    host="$(printf '%s' "${host}" | tr -d '\r')"
    case "${host}" in
      ''|'#'*)
        continue
        ;;
    esac
    HOSTS+=("${host}")
  done < "${HOSTS_FILE}"
  if [ "${#HOSTS[@]}" -eq 0 ]; then
    echo "主机清单为空: ${HOSTS_FILE}" >&2
    exit 1
  fi
}

sync_one_host() {
  local host="$1"
  echo "[${host}] 同步脚本"
  remote_exec "${host}" "mkdir -p '${REMOTE_BASE}' '${REMOTE_LOG_DIR}'"
  scp ${SSH_OPTS} \
    "${SCRIPT_DIR}/cpu_pressure.sh" \
    "${SCRIPT_DIR}/memory_pressure.sh" \
    "${SCRIPT_DIR}/disk_pressure.sh" \
    "${host}:${REMOTE_BASE}/" </dev/null
  remote_exec "${host}" "chmod +x '${REMOTE_BASE}/cpu_pressure.sh' '${REMOTE_BASE}/memory_pressure.sh' '${REMOTE_BASE}/disk_pressure.sh'"
}

start_remote_script() {
  local host="$1"
  local script_path="$2"
  local log_file="$3"
  local start_command="$4"
  remote_exec "${host}" "
    find_script_pids() {
      ps -eo pid=,args= | awk -v needle='${script_path}' -v self=\"\$\$\" '
        index(\$0, needle) && \$1 != self && index(\$0, \"awk -v needle\") == 0 { print \$1 }
      '
    }
    if [ -n \"\$(find_script_pids)\" ]; then
      echo 'already running'
    else
      mkdir -p '${REMOTE_LOG_DIR}' '${REMOTE_DISK_TARGET}'
      setsid -f bash -lc \"${start_command}\"
      sleep 1
      if [ -n \"\$(find_script_pids)\" ]; then
        echo 'started'
      else
        echo 'start failed'
        [ -f '${REMOTE_LOG_DIR}/${log_file}' ] && tail -n 20 '${REMOTE_LOG_DIR}/${log_file}' || true
        exit 1
      fi
    fi
  "
}

start_one_host() {
  local host="$1"
  sync_one_host "${host}"
  echo "[${host}] 启动远端压力脚本"
  start_remote_script "${host}" "${REMOTE_BASE}/cpu_pressure.sh" "cpu_pressure.log" "nohup env MIN_PERCENT='${MIN_PERCENT}' MAX_PERCENT='${MAX_PERCENT}' CHECK_INTERVAL='${CHECK_INTERVAL:-3}' CPU_GAIN='${CPU_GAIN:-1.8}' MAX_WORKERS='${MAX_WORKERS:-}' bash '${REMOTE_BASE}/cpu_pressure.sh' >> '${REMOTE_LOG_DIR}/cpu_pressure.log' 2>&1 </dev/null"
  start_remote_script "${host}" "${REMOTE_BASE}/memory_pressure.sh" "memory_pressure.log" "nohup env MIN_PERCENT='${MIN_PERCENT}' MAX_PERCENT='${MAX_PERCENT}' CHECK_INTERVAL='${CHECK_INTERVAL:-3}' bash '${REMOTE_BASE}/memory_pressure.sh' >> '${REMOTE_LOG_DIR}/memory_pressure.log' 2>&1 </dev/null"
  start_remote_script "${host}" "${REMOTE_BASE}/disk_pressure.sh" "disk_pressure.log" "nohup env MIN_PERCENT='${MIN_PERCENT}' MAX_PERCENT='${MAX_PERCENT}' CHUNK_MB='${CHUNK_MB:-256}' TARGET_PATH='${REMOTE_DISK_TARGET}' bash '${REMOTE_BASE}/disk_pressure.sh' >> '${REMOTE_LOG_DIR}/disk_pressure.log' 2>&1 </dev/null"
}

status_one_host() {
  local host="$1"
  echo "===== ${host} ====="
  remote_exec "${host}" "
    find_script_pids() {
      local script_path=\"\$1\"
      ps -eo pid=,args= | awk -v needle=\"\${script_path}\" -v self=\"\$\$\" '
        index(\$0, needle) && \$1 != self && index(\$0, \"awk -v needle\") == 0 { print \$1 }
      '
    }
    cpu_pid=\$(find_script_pids '${REMOTE_BASE}/cpu_pressure.sh' | tr '\n' ' ' || true)
    mem_pid=\$(find_script_pids '${REMOTE_BASE}/memory_pressure.sh' | tr '\n' ' ' || true)
    disk_pid=\$(find_script_pids '${REMOTE_BASE}/disk_pressure.sh' | tr '\n' ' ' || true)
    echo \"cpu_pressure.sh: \${cpu_pid:-not running}\"
    echo \"memory_pressure.sh: \${mem_pid:-not running}\"
    echo \"disk_pressure.sh: \${disk_pid:-not running}\"
    [ -f '${REMOTE_LOG_DIR}/cpu_pressure.log' ] && echo '--- cpu log ---' && tail -n 5 '${REMOTE_LOG_DIR}/cpu_pressure.log' || true
    [ -f '${REMOTE_LOG_DIR}/memory_pressure.log' ] && echo '--- memory log ---' && tail -n 5 '${REMOTE_LOG_DIR}/memory_pressure.log' || true
    [ -f '${REMOTE_LOG_DIR}/disk_pressure.log' ] && echo '--- disk log ---' && tail -n 5 '${REMOTE_LOG_DIR}/disk_pressure.log' || true
  "
}

stop_remote_script() {
  local host="$1"
  local script_path="$2"
  local state_dir="$3"
  remote_exec "${host}" "
    find_script_pids() {
      ps -eo pid=,args= | awk -v needle='${script_path}' -v self=\"\$\$\" '
        index(\$0, needle) && \$1 != self && index(\$0, \"awk -v needle\") == 0 { print \$1 }
      '
    }
    pids=\$(find_script_pids || true)
    if [ -n \"\${pids}\" ]; then
      for pid in \${pids}; do
        kill \"\${pid}\" 2>/dev/null || true
      done
      sleep 1
      remaining=\$(find_script_pids || true)
      if [ -n \"\${remaining}\" ]; then
        for pid in \${remaining}; do
          kill -9 \"\${pid}\" 2>/dev/null || true
        done
      fi
    fi
    if [ -n '${state_dir}' ] && [ -d '${state_dir}' ]; then
      find '${state_dir}' -type f -name '*.pid' | while read -r pid_file; do
        worker_pid=\$(cat \"\${pid_file}\" 2>/dev/null || true)
        if [ -n \"\${worker_pid}\" ]; then
          kill \"\${worker_pid}\" 2>/dev/null || true
          kill -9 \"\${worker_pid}\" 2>/dev/null || true
        fi
      done
      rm -rf '${state_dir}' 2>/dev/null || true
    fi
  "
}

stop_one_host() {
  local host="$1"
  echo "[${host}] 停止远端压力脚本"
  stop_remote_script "${host}" "${REMOTE_BASE}/cpu_pressure.sh" "${REMOTE_CPU_STATE_ROOT}"
  stop_remote_script "${host}" "${REMOTE_BASE}/memory_pressure.sh" "${REMOTE_MEMORY_STATE_DIR}"
  stop_remote_script "${host}" "${REMOTE_BASE}/disk_pressure.sh" ""
}

cleanup_disk_one_host() {
  local host="$1"
  echo "[${host}] 清理磁盘压力文件"
  remote_exec "${host}" "
    if [ -x '${REMOTE_BASE}/disk_pressure.sh' ]; then
      TARGET_PATH='${REMOTE_DISK_TARGET}' bash '${REMOTE_BASE}/disk_pressure.sh' --cleanup
    else
      rm -f '${REMOTE_DISK_TARGET}'/disk-pressure-*.bin || true
    fi
  "
}

percent_in_range() {
  local value="$1"
  awk -v current="${value}" -v min="${MIN_PERCENT}" -v max="${MAX_PERCENT}" 'BEGIN {
    if (current == "" || current == "N/A") exit 1;
    exit !(current >= min && current <= max);
  }'
}

format_ratio() {
  local ok_count="$1"
  local total_count="$2"
  awk -v ok="${ok_count}" -v total="${total_count}" 'BEGIN {
    if (total <= 0) {
      print "0/0 (0.00%)";
      exit;
    }
    printf "%d/%d (%.2f%%)\n", ok, total, ok * 100 / total;
  }'
}

collect_status_snapshot() {
  local host="$1"
  remote_exec "${host}" "
    find_script_pids() {
      local script_path=\"\$1\"
      ps -eo pid=,args= | awk -v needle=\"\${script_path}\" -v self=\"\$\$\" '
        index(\$0, needle) && \$1 != self && index(\$0, \"awk -v needle\") == 0 { print \$1 }
      '
    }
    cpu_pid=\$(find_script_pids '${REMOTE_BASE}/cpu_pressure.sh' | tr '\n' ' ' || true)
    mem_pid=\$(find_script_pids '${REMOTE_BASE}/memory_pressure.sh' | tr '\n' ' ' || true)
    disk_pid=\$(find_script_pids '${REMOTE_BASE}/disk_pressure.sh' | tr '\n' ' ' || true)

    read_cpu_percent() {
      read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
      total1=\$((user + nice + system + idle + iowait + irq + softirq + steal))
      idle1=\$((idle + iowait))
      sleep 1
      read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat
      total2=\$((user + nice + system + idle + iowait + irq + softirq + steal))
      idle2=\$((idle + iowait))
      awk -v total1=\"\${total1}\" -v total2=\"\${total2}\" -v idle1=\"\${idle1}\" -v idle2=\"\${idle2}\" 'BEGIN {
        total = total2 - total1;
        idle = idle2 - idle1;
        if (total <= 0) { print \"N/A\"; exit }
        used = (1 - idle / total) * 100;
        if (used < 0) used = 0;
        printf \"%.2f\", used;
      }'
    }

    read_mem_percent() {
      awk '
        /^MemTotal:/ { total = \$2 * 1024 }
        /^MemAvailable:/ { available = \$2 * 1024 }
        END {
          if (total <= 0) {
            print \"N/A\";
            exit;
          }
          used = total - available;
          if (used < 0) used = 0;
          printf \"%.2f\", used * 100 / total;
        }
      ' /proc/meminfo
    }

    resolve_disk_path() {
      target='${REMOTE_DISK_TARGET}'
      if [ -e \"\${target}\" ]; then
        printf '%s\n' \"\${target}\"
        return
      fi
      probe=\"\${target}\"
      while [ \"\${probe}\" != \"/\" ]; do
        probe=\"\$(dirname \"\${probe}\")\"
        if [ -e \"\${probe}\" ]; then
          printf '%s\n' \"\${probe}\"
          return
        fi
      done
      printf '/\n'
    }

    read_disk_percent() {
      disk_probe=\"\$(resolve_disk_path)\"
      df -P \"\${disk_probe}\" 2>/dev/null | awk 'NR==2 { gsub(/%/, \"\", \$5); print \$5; found=1 } END { if (!found) print \"N/A\" }'
    }

    echo \"cpu_pid=\${cpu_pid:-not running}\"
    echo \"memory_pid=\${mem_pid:-not running}\"
    echo \"disk_pid=\${disk_pid:-not running}\"
    echo \"cpu_percent=\$(read_cpu_percent)\"
    echo \"memory_percent=\$(read_mem_percent)\"
    echo \"disk_percent=\$(read_disk_percent)\"
  "
}

status_command() {
  local total_hosts=0
  local cpu_ok=0
  local memory_ok=0
  local disk_ok=0

  for host in "${HOSTS[@]}"; do
    total_hosts=$((total_hosts + 1))
    local snapshot=""
    local cpu_pid="not running"
    local memory_pid="not running"
    local disk_pid="not running"
    local cpu_percent="N/A"
    local memory_percent="N/A"
    local disk_percent="N/A"
    local cpu_status="FAIL"
    local memory_status="FAIL"
    local disk_status="FAIL"

    echo "===== ${host} ====="
    if ! snapshot="$(collect_status_snapshot "${host}")"; then
      echo "状态采集失败"
      continue
    fi

    while IFS='=' read -r key value; do
      case "${key}" in
        cpu_pid) cpu_pid="${value}" ;;
        memory_pid) memory_pid="${value}" ;;
        disk_pid) disk_pid="${value}" ;;
        cpu_percent) cpu_percent="${value}" ;;
        memory_percent) memory_percent="${value}" ;;
        disk_percent) disk_percent="${value}" ;;
      esac
    done <<EOF
${snapshot}
EOF

    if percent_in_range "${cpu_percent}"; then
      cpu_status="OK"
      cpu_ok=$((cpu_ok + 1))
    fi
    if percent_in_range "${memory_percent}"; then
      memory_status="OK"
      memory_ok=$((memory_ok + 1))
    fi
    if percent_in_range "${disk_percent}"; then
      disk_status="OK"
      disk_ok=$((disk_ok + 1))
    fi

    echo "cpu_pressure.sh: ${cpu_pid}"
    echo "memory_pressure.sh: ${memory_pid}"
    echo "disk_pressure.sh: ${disk_pid}"
    echo "CPU 使用率: ${cpu_percent}% | 达标: ${cpu_status}"
    echo "内存使用率: ${memory_percent}% | 达标: ${memory_status}"
    echo "磁盘使用率: ${disk_percent}% | 达标: ${disk_status}"
  done

  echo "===== 汇总 ====="
  echo "目标区间: ${MIN_PERCENT}% - ${MAX_PERCENT}%"
  echo "CPU 达标率: $(format_ratio "${cpu_ok}" "${total_hosts}")"
  echo "内存达标率: $(format_ratio "${memory_ok}" "${total_hosts}")"
  echo "磁盘达标率: $(format_ratio "${disk_ok}" "${total_hosts}")"
}

main() {
  ensure_dependencies
  local action="${1:-}"
  if [ -z "${action}" ]; then
    usage
    exit 1
  fi
  if [ $# -ge 2 ]; then
    HOSTS_FILE="$2"
  fi
  load_hosts

  case "${action}" in
    start)
      for host in "${HOSTS[@]}"; do
        start_one_host "${host}"
      done
      ;;
    status)
      status_command
      ;;
    stop)
      for host in "${HOSTS[@]}"; do
        stop_one_host "${host}"
      done
      ;;
    cleanup-disk)
      for host in "${HOSTS[@]}"; do
        cleanup_disk_one_host "${host}"
      done
      ;;
    sync)
      for host in "${HOSTS[@]}"; do
        sync_one_host "${host}"
      done
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
