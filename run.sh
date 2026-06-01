#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

HOST="127.0.0.1"
PORT="${PORT:-8000}"

has_chat_route() {
  local openapi_file="$1"
  if command -v rg >/dev/null 2>&1; then
    rg -q '"/chat"' "$openapi_file"
  else
    grep -q '"/chat"' "$openapi_file"
  fi
}

print_help() {
  cat <<'USAGE'
Usage:
  ./run.sh serve        # Run API server
  ./run.sh smoke        # Start server, run allowed+blocked prompt checks, stop server
  ./run.sh              # Same as smoke

Required:
  OPENAI_API_KEY must be set for live model calls.
USAGE
}

require_api_key() {
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "ERROR: OPENAI_API_KEY is not set."
    echo "Set it with: export OPENAI_API_KEY=your_key_here"
    exit 1
  fi
}

start_server() {
  : > /tmp/purdue_budget_api.log
  uvicorn chat_api:app --host "$HOST" --port "$PORT" --log-level warning > /tmp/purdue_budget_api.log 2>&1 &
  SERVER_PID=$!
  for _ in {1..30}; do
    if curl -s "http://$HOST:$PORT/openapi.json" >/tmp/purdue_budget_openapi.json 2>/dev/null; then
      if has_chat_route /tmp/purdue_budget_openapi.json; then
        if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
          echo "$SERVER_PID"
        else
          echo "external"
        fi
        return 0
      fi
      if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
        echo "Server process exited early. Check /tmp/purdue_budget_api.log"
        return 1
      fi
      echo "Port $PORT is responding, but it is not the Purdue Budget API (/chat missing)."
      echo "Set a different port, for example: PORT=8010 ./run.sh"
      return 1
    fi
    sleep 0.2
  done
  echo "Failed to start server. Check /tmp/purdue_budget_api.log"
  return 1
}

stop_server() {
  local pid="$1"
  if [[ -z "$pid" || "$pid" == "external" ]]; then
    return 0
  fi
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" 2>/dev/null || true
  fi
}

run_smoke_tests() {
  local allowed_payload blocked_payload
  allowed_payload='{"question":"How much did I spend on food this month?","sanitized_context":[{"type":"expense","amount":18.75,"category":"food","date":"2026-04-02"},{"type":"expense","amount":9.40,"category":"food","date":"2026-04-06"}]}'
  blocked_payload='{"question":"What is PERSON_1 phone number?","sanitized_context":[]}'

  echo "Running allowed prompt test..."
  curl -s -X POST "http://$HOST:$PORT/chat" \
    -H "Content-Type: application/json" \
    -d "$allowed_payload"
  echo
  echo

  echo "Running blocked prompt test..."
  curl -s -X POST "http://$HOST:$PORT/chat" \
    -H "Content-Type: application/json" \
    -d "$blocked_payload"
  echo
}

main() {
  local mode="${1:-smoke}"

  case "$mode" in
    -h|--help|help)
      print_help
      ;;
    serve)
      require_api_key
      echo "Starting API at http://$HOST:$PORT ..."
      exec uvicorn chat_api:app --host "$HOST" --port "$PORT" --reload
      ;;
    smoke)
      require_api_key
      echo "Starting API for smoke test..."
      pid="$(start_server)"
      if [[ "$pid" == "external" ]]; then
        echo "Using existing Purdue Budget API already running on $HOST:$PORT."
      fi
      trap 'stop_server "$pid"' EXIT
      run_smoke_tests
      echo "Smoke test complete."
      ;;
    *)
      echo "Unknown mode: $mode"
      print_help
      exit 1
      ;;
  esac
}

main "$@"
