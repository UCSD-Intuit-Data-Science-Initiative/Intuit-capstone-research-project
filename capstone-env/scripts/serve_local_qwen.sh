#!/usr/bin/env bash
# Start a local Qwen 3.5 9B server on the host for configs/local_qwen.yaml. Leave it running in its own
# terminal while scripts/run_local_qwen.sh runs; Ctrl-C stops it. Run one server at a time (they share the GPU).
# Usage: scripts/serve_local_qwen.sh ollama|llama
#   ollama  needs `ollama pull qwen3.5:9b`
#   llama   needs `brew install llama.cpp` and the GGUF (unsloth/Qwen3.5-9B-GGUF, Qwen3.5-9B-Q4_K_M.gguf);
#           GGUF=<path> overrides the default ~/models/Qwen3.5-9B-Q4_K_M.gguf
set -euo pipefail
KEEP_AWAKE=(); command -v caffeinate >/dev/null && KEEP_AWAKE=(caffeinate -is)   # macOS: no sleep mid-run
case "${1:-}" in
  ollama)
    # 32k context: Ollama's default can be shorter than these conversations (up to about 17k tokens), and a
    # prompt cut to fit gives no error.
    export OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_HOST=127.0.0.1:11435
    exec ${KEEP_AWAKE[@]+"${KEEP_AWAKE[@]}"} ollama serve ;;
  llama)
    GGUF=${GGUF:-$HOME/models/Qwen3.5-9B-Q4_K_M.gguf}
    [ -f "$GGUF" ] || { echo "model file not found: $GGUF (set GGUF=...)"; exit 1; }
    # 4 slots of 32k context each; --jinja applies the model's chat template (tool calls, thinking).
    exec ${KEEP_AWAKE[@]+"${KEEP_AWAKE[@]}"} llama-server -m "$GGUF" --jinja -np 4 -c 131072 -fa on \
      --host 127.0.0.1 --port 11437 --alias qwen3.5:9b ;;
  *) echo "usage: $0 ollama|llama"; exit 2 ;;
esac
