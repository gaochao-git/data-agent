#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-3030}"
DATASET="${DATASET:-zhidu}"
FUSEKI_HOME="${FUSEKI_HOME:-/opt/apache-jena-fuseki}"
DB_DIR="${DB_DIR:-/data/aigc/jena/fuseki-db}"
LOG_DIR="${LOG_DIR:-/data/aigc/jena/fuseki-logs}"
PID_FILE="${PID_FILE:-$LOG_DIR/${DATASET}.pid}"
TTL_FILE="${TTL_FILE:-}"
QUERY_TEXT="${QUERY_TEXT:-}"
QUERY_FILE="${QUERY_FILE:-}"
ACCEPT="${ACCEPT:-application/sparql-results+json}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-30}"

usage() {
  cat <<'EOF'
Usage:
  jena_ops.sh <command> [options]

Commands:
  start       Start Fuseki in background
  stop        Stop Fuseki process by PID file
  status      Show process status
  health      Check query endpoint health
  import      Import a TTL file into dataset default graph
  query       Execute a SPARQL query (text or file)
  count       Count triples
  tail-log    Tail server log

Options:
  --host <host>               Default: 0.0.0.0
  --port <port>               Default: 3030
  --dataset <name>            Default: zhidu
  --fuseki-home <dir>         Default: /opt/apache-jena-fuseki
  --db-dir <dir>              Default: /data/aigc/jena/fuseki-db
  --log-dir <dir>             Default: /data/aigc/jena/fuseki-logs
  --pid-file <file>           Override PID file path
  --ttl <file>                TTL file for import command
  --query "<sparql>"          SPARQL text for query command
  --query-file <file>         SPARQL file for query command
  --accept <mime>             Default: application/sparql-results+json
  --timeout <seconds>         Default: 30

Examples:
  ./scripts/graph_ops/jena_ops.sh start --fuseki-home /opt/apache-jena-fuseki-4.10.0
  ./scripts/graph_ops/jena_ops.sh import --ttl /data/aigc/zhidu_0407/output/08_clause_semantics_ontology/clause_semantics_ontology.ttl
  ./scripts/graph_ops/jena_ops.sh count
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
      --dataset) DATASET="$2"; shift 2 ;;
      --fuseki-home) FUSEKI_HOME="$2"; shift 2 ;;
      --db-dir) DB_DIR="$2"; shift 2 ;;
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

query_endpoint() {
  echo "http://${HOST}:${PORT}/${DATASET}/query"
}

data_endpoint() {
  echo "http://${HOST}:${PORT}/${DATASET}/data?default"
}

start_server() {
  local fuseki_bin="${FUSEKI_HOME}/fuseki-server"
  if [[ ! -x "$fuseki_bin" ]]; then
    echo "Fuseki binary not found or not executable: $fuseki_bin" >&2
    exit 1
  fi
  mkdir -p "$DB_DIR" "$LOG_DIR"
  if is_running; then
    echo "Fuseki already running, pid=$(cat "$PID_FILE")"
    return
  fi
  nohup "$fuseki_bin" --update --loc="$DB_DIR" "/${DATASET}" > "${LOG_DIR}/${DATASET}.out" 2>&1 &
  echo $! > "$PID_FILE"
  echo "Started Fuseki pid=$(cat "$PID_FILE")"
  echo "Log file: ${LOG_DIR}/${DATASET}.out"
}

stop_server() {
  if ! is_running; then
    echo "Fuseki is not running"
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
  echo "Stopped Fuseki pid=$pid"
}

status_server() {
  if is_running; then
    echo "Fuseki running, pid=$(cat "$PID_FILE")"
  else
    echo "Fuseki not running"
  fi
}

health_check() {
  curl -fsS -m "$TIMEOUT_SECONDS" -G "$(query_endpoint)" --data-urlencode 'query=SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }' -H "Accept: ${ACCEPT}" >/dev/null
  echo "OK: $(query_endpoint)"
}

import_ttl() {
  if [[ -z "$TTL_FILE" ]]; then
    echo "--ttl is required for import command" >&2
    exit 1
  fi
  require_file "$TTL_FILE"
  curl -fsS -m "$TIMEOUT_SECONDS" -X POST -H "Content-Type: text/turtle" --data-binary "@${TTL_FILE}" "$(data_endpoint)"
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
  curl -fsS -m "$TIMEOUT_SECONDS" -G "$(query_endpoint)" --data-urlencode "query=${query}" -H "Accept: ${ACCEPT}"
  echo
}

count_triples() {
  QUERY_TEXT='SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }'
  run_query
}

tail_log() {
  mkdir -p "$LOG_DIR"
  touch "${LOG_DIR}/${DATASET}.out"
  tail -f "${LOG_DIR}/${DATASET}.out"
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
    import) import_ttl ;;
    query) run_query ;;
    count) count_triples ;;
    tail-log) tail_log ;;
    -h|--help|help) usage ;;
    *) echo "Unknown command: $command" >&2; usage; exit 1 ;;
  esac
}

main "$@"
