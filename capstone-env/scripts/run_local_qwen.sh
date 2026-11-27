#!/usr/bin/env bash
# Run the credit families on local Qwen 3.5 9B (configs/local_qwen.yaml), graded and sent to Langfuse.
# Start the server first: scripts/serve_local_qwen.sh ollama|llama (in another terminal).
# Usage: scripts/run_local_qwen.sh ollama|llama [K] [family dirs...]
#   K defaults to 3; families default to data/families/smoke data/families/pilot (all four).
#   NO_LANGFUSE=1 skips the Langfuse copy (send it later with `cli langfuse <results folder>`).
# Output: results/<date>_<HHMM>_<families>_k<K>_qwen3.5-9b_<backend>/ with the models file used and run.log.
set -euo pipefail
cd "$(dirname "$0")/.."
BACKEND=${1:-}; K=${2:-3}; shift $(( $# < 2 ? $# : 2 ))
if [ $# -gt 0 ]; then FAMILIES=("$@"); else FAMILIES=(data/families/smoke data/families/pilot); fi
case "$BACKEND" in
  ollama) MODEL=ollama-qwen9b PORT=11435 WORKERS=1 TAG=ollama ;;
  llama)  MODEL=llama-qwen9b PORT=11437 WORKERS=4 TAG=llama-np4 ;;
  *) echo "usage: $0 ollama|llama [K] [family dirs...]"; exit 2 ;;
esac
# Fail now, not after every run has errored: the server must be up and serving qwen3.5:9b.
curl -sf -m 5 "http://127.0.0.1:$PORT/v1/models" | grep -q '"qwen3.5:9b"' || {
  echo "no qwen3.5:9b server on port $PORT; start it with scripts/serve_local_qwen.sh $BACKEND"; exit 1; }
grep -q '^OLLAMA_API_KEY=.' .env 2>/dev/null || { echo "set OLLAMA_API_KEY=<any value> in .env"; exit 1; }
NAMES=$(for f in "${FAMILIES[@]}"; do basename "$f"; done | paste -sd+ -)
D=results/$(date +%F_%H%M)_${NAMES}_k${K}_qwen3.5-9b_${TAG}
mkdir -p "$D" && cp configs/local_qwen.yaml "$D/models.yaml"
LANGFUSE=(--langfuse); [ -n "${NO_LANGFUSE:-}" ] && LANGFUSE=()
echo "writing $D (branch $(git branch --show-current), commit $(git rev-parse --short HEAD))" | tee "$D/run.log"
KEEP_AWAKE=(); command -v caffeinate >/dev/null && KEEP_AWAKE=(caffeinate -is)
GID=$(id -g) ${KEEP_AWAKE[@]+"${KEEP_AWAKE[@]}"} docker compose run --rm dev python -m capstone.cli run-families \
  --families "${FAMILIES[@]}" --model "$MODEL" --models-config "$D/models.yaml" --k "$K" --workers "$WORKERS" \
  --seconds-per-turn 1800 --out "$D" ${LANGFUSE[@]+"${LANGFUSE[@]}"} 2>&1 | tee -a "$D/run.log"
