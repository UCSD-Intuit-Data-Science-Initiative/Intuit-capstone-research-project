#!/usr/bin/env bash
# Run bench_parallel.py against llama-server (llama.cpp) and mlx_lm.server set-ups, one at a time.
# Usage: scripts/bench_llama_mlx.sh <traces dir> <out.jsonl> <log dir> <model.gguf> <mlx model dir> <mlx venv>
# Sampling matches Ollama's qwen3.5:9b defaults (temp 1, top_k 20, top_p 0.95, presence_penalty 1.5).
set -u
TRACES=$1; OUT=$2; LOGS=$3; GGUF=$4; MLX_MODEL=$5; VENV=$6; HERE=$(cd "$(dirname "$0")" && pwd)
CONV=${CONV:-8}; CALLS=${CALLS:-4}; PORT=11437
SAMPLING='{"temperature":1,"top_p":0.95,"top_k":20,"presence_penalty":1.5}'
BENCH=(python3 "$HERE/bench_parallel.py" --traces "$TRACES" --conversations "$CONV" --calls "$CALLS" --out "$OUT"
       --base-url "http://127.0.0.1:$PORT/v1" --extra "$SAMPLING")

wait_up() { for _ in $(seq 1 120); do curl -sf -m 2 "http://127.0.0.1:$PORT/$1" >/dev/null && return; sleep 2; done
            echo "server did not start"; return 1; }
stop() { local pids; pids=$(lsof -ti "tcp:$PORT" -sTCP:LISTEN); [ -n "$pids" ] && kill $pids; sleep 5; }
run() { echo "== $1"; "${BENCH[@]}" --model "$2" --label "$1" --concurrency "$3" | grep -E '"(wall_s|out_tok_per_s|req_per_min|errors|latency_median_s|first_errors)"'; }

llama() {  # llama <slots> [extra flags...]
  local np=$1; shift
  llama-server -m "$GGUF" --jinja -np "$np" -c $((32768 * np)) -fa on --host 127.0.0.1 --port $PORT \
    --alias qwen3.5:9b "$@" > "$LOGS/llama_np${np}.log" 2>&1 &
  wait_up health
}
mlx() {  # mlx <concurrency>
  "$VENV/bin/mlx_lm.server" --model "$MLX_MODEL" --host 127.0.0.1 --port $PORT --decode-concurrency "$1" \
    --prompt-concurrency "$1" --prompt-cache-size 16 > "$LOGS/mlx_c$1.log" 2>&1 &
  wait_up v1/models
}

stop
llama 1 && run llama-np1-c1 qwen3.5:9b 1; stop
llama 4 && run llama-np4-c4 qwen3.5:9b 4; stop
llama 8 && run llama-np8-c8 qwen3.5:9b 8; stop
mlx 1 && run mlx-c1 "$MLX_MODEL" 1; stop
mlx 4 && run mlx-c4 "$MLX_MODEL" 4; stop
mlx 8 && run mlx-c8 "$MLX_MODEL" 8; stop
echo done
