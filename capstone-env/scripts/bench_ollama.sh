#!/usr/bin/env bash
# Run bench_parallel.py against several Ollama set-ups, one at a time (each gets the whole GPU).
# Usage: scripts/bench_ollama.sh <traces dir> <out.jsonl> <log dir>
# Test servers use ports 11436-11439 so the default Ollama (11434) and the run server (11435) are untouched.
set -u
TRACES=$1; OUT=$2; LOGS=$3; HERE=$(cd "$(dirname "$0")" && pwd)
CONV=${CONV:-8}; CALLS=${CALLS:-4}
BENCH=(python3 "$HERE/bench_parallel.py" --traces "$TRACES" --conversations "$CONV" --calls "$CALLS" --model qwen3.5:9b --out "$OUT")

start() {  # start <port> <parallel> [extra env...]
  local port=$1 par=$2; shift 2
  env OLLAMA_HOST=127.0.0.1:$port OLLAMA_NUM_PARALLEL=$par OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_KEEP_ALIVE=30m "$@" \
    ollama serve > "$LOGS/ollama_${port}_p${par}.log" 2>&1 &
  for _ in $(seq 1 30); do curl -s -m 2 "http://127.0.0.1:$port/api/version" >/dev/null && return; sleep 1; done
  echo "server on $port did not start"; return 1
}
stop() { local pids; for port in "$@"; do pids=$(lsof -ti "tcp:$port" -sTCP:LISTEN); [ -n "$pids" ] && kill $pids; done; sleep 3; }
warm() { curl -s -m 300 "http://127.0.0.1:$1/api/generate" -d '{"model":"qwen3.5:9b","prompt":"hi","stream":false,"options":{"num_predict":1}}' >/dev/null; }

run() {  # run <label> <concurrency> <urls>
  echo "== $1"; "${BENCH[@]}" --label "$1" --concurrency "$2" --base-url "$3" | grep -E '"(wall_s|out_tok_per_s|req_per_min|errors|latency_median_s)"'
}

stop 11436 11437 11438 11439
start 11436 1 && warm 11436
run ollama-p1-c1 1 http://127.0.0.1:11436/v1
run ollama-p1-c4 4 http://127.0.0.1:11436/v1
stop 11436

start 11436 4 && warm 11436
run ollama-p4-c4 4 http://127.0.0.1:11436/v1
stop 11436

start 11436 8 && warm 11436
run ollama-p8-c8 8 http://127.0.0.1:11436/v1
stop 11436

for p in 11436 11437 11438 11439; do start $p 1 && warm $p; done
run ollama-4servers-c4 4 http://127.0.0.1:11436/v1,http://127.0.0.1:11437/v1,http://127.0.0.1:11438/v1,http://127.0.0.1:11439/v1
stop 11436 11437 11438 11439
echo done
