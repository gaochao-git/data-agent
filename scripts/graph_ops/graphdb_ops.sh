#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-7200}"
REPO="${REPO:-zhidu}"
GRAPHDB_HOME="${GRAPHDB_HOME:-/opt/graphdb}"
LOG_DIR="${LOG_DIR:-/data/aigc/graphdb/logs}"
PID_FILE="${PID_FILE:-$LOG_DIR/graphdb.pid}"
TTL_FILE="${TTL_FILE:-}"
QUERY_TEXT="${QUERY_TEXT:-}"
QUERY_FILE="${QUERY_FILE:-}"
ACCEPT="${ACCEPT:-application/sparql-results+json}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-30}"

usage() {
  cat <<'EOF'
Usage:
  graphdb_ops.sh <command> [options]

Commands:
  start       Start GraphDB in background (dist package)
  stop        Stop GraphDB process by PID file
  status      Show process status
  health      Check repository endpoint health
  list-repos  List repositories
  import      Import a TTL file into repository
  query       Execute a SPARQL query (text or file)
  count       Count triples
  tail-log    Tail GraphDB log

Options:
  --host <host>               Default: 0.0.0.0
  --port <port>               Default: 7200
  --repo <repo-id>            Default: zhidu
  --graphdb-home <dir>        Default: /opt/graphdb
  --log-dir <dir>             Default: /data/aigc/graphdb/logs
  --pid-file <file>           Override PID file path
  --ttl <file>                TTL file for import command
  --query "<sparql>"          SPARQL text for query command
  --query-file <file>         SPARQL file for query command
  --accept <mime>             Default: application/sparql-results+json
  --timeout <seconds>         Default: 30

Examples:
  ./scripts/graph_ops/graphdb_ops.sh start --graphdb-home /opt/graphdb-11.3.2
  ./scripts/graph_ops/graphdb_ops.sh import --repo zhidu --ttl /data/aigc/zhidu_0407/output/08_clause_semantics_ontology/clause_semantics_ontology.graphdb_ready.ttl
  ./scripts/graph_ops/graphdb_ops.sh count --repo zhidu
EOF
}

require_file() {
  local file_path="$1"
  if [[ ! -f "$file_path" ]]; then
    echo "File not found: $file_path" >&2
    exit 1
  fi
}

parse_options() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --host) HOST="$2"; shift 2 ;;
      --port) PORT="$2"; shift 2 ;;
      --repo) REPO="$2"; shift 2 ;;
      --graphdb-home) GRAPHDB_HOME="$2"; shift 2 ;;
      --log-dir) LOG_DIR="$2"; shift 2 ;;
      --pid-file) PID_FILE="$2"; shift 2 ;;
      --ttl) TTL_FILE="$2"; shift 2 ;;
      --query) QUERY_TEXT="$2"; shift 2 ;;
      --query-file) QUERY_FILE="$2"; shift 2 ;;
      --accept) ACCEPT="$2"; shift 2 ;;
      --timeout) TIMEOUT_SECONDS="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
  done
}

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  ps -p "$pid" >/dev/null 2>&1
}

repo_endpoint() {
  echo "http://${HOST}:${PORT}/repositories/${REPO}"
}

statements_endpoint() {
  echo "http://${HOST}:${PORT}/repositories/${REPO}/statements"
}

start_server() {
  local graphdb_bin="${GRAPHDB_HOME}/bin/graphdb"
  if [[ ! -x "$graphdb_bin" ]]; then
    echo "GraphDB binary not found or not executable: $graphdb_bin" >&2
    exit 1
  fi
  mkdir -p "$LOG_DIR"
  if is_running; then
    echo "GraphDB already running, pid=$(cat "$PID_FILE")"
    return
  fi
  nohup "$graphdb_bin" > "${LOG_DIR}/graphdb.out" 2>&1 &
  echo $! > "$PID_FILE"
  echo "Started GraphDB pid=$(cat "$PID_FILE")"
  echo "Log file: ${LOG_DIR}/graphdb.out"
}

stop_server() {
  if ! is_running; then
    echo "GraphDB is not running"
    rm -f "$PID_FILE"
    return
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid"
  sleep 1
  if ps -p "$pid" >/dev/null 2>&1; then
    kill -9 "$pid"
  fi
  rm -f "$PID_FILE"
  echo "Stopped GraphDB pid=$pid"
}

status_server() {
  if is_running; then
    echo "GraphDB running, pid=$(cat "$PID_FILE")"
  else
    echo "GraphDB not running"
  fi
}

health_check() {
  curl -fsS -m "$TIMEOUT_SECONDS" "$(repo_endpoint)" -H "Accept: ${ACCEPT}" >/dev/null
  echo "OK: $(repo_endpoint)"
}

list_repositories() {
  curl -fsS -m "$TIMEOUT_SECONDS" "http://${HOST}:${PORT}/repositories"
  echo
}

import_ttl() {
  if [[ -z "$TTL_FILE" ]]; then
    echo "--ttl is required for import command" >&2
    exit 1
  fi
  require_file "$TTL_FILE"
  curl -fsS -m "$TIMEOUT_SECONDS" -X POST -H "Content-Type: text/turtle" --data-binary "@${TTL_FILE}" "$(statements_endpoint)"
  echo
  echo "Imported: $TTL_FILE"
}

run_query() {
  local query=""
  if [[ -n "$QUERY_TEXT" ]]; then
    query="$QUERY_TEXT"
  elif [[ -n "$QUERY_FILE" ]]; then
    require_file "$QUERY_FILE"
    query="$(cat "$QUERY_FILE")"
  else
    echo "--query or --query-file is required for query command" >&2
    exit 1
  fi
  curl -fsS -m "$TIMEOUT_SECONDS" -G "$(repo_endpoint)" --data-urlencode "query=${query}" -H "Accept: ${ACCEPT}"
  echo
}

count_triples() {
  QUERY_TEXT='SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }'
  run_query
}

tail_log() {
  mkdir -p "$LOG_DIR"
  touch "${LOG_DIR}/graphdb.out"
  tail -f "${LOG_DIR}/graphdb.out"
}

main() {
  local command="${1:-}"
  if [[ -z "$command" ]]; then
    usage
    exit 1
  fi
  shift || true
  parse_options "$@"
  case "$command" in
    start) start_server ;;
    stop) stop_server ;;
    status) status_server ;;
    health) health_check ;;
    list-repos) list_repositories ;;
    import) import_ttl ;;
    query) run_query ;;
    count) count_triples ;;
    tail-log) tail_log ;;
    -h|--help|help) usage ;;
    *) echo "Unknown command: $command" >&2; usage; exit 1 ;;
  esac
}

main "$@"
