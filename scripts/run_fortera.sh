#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${FORTERA_MODEL_PATH:-$HERE/Fortera-Q4_K_M.gguf}"
BIN="${LLAMA_SERVER_BIN:-llama-server}"
[[ -f "$MODEL" ]] || { echo "Model not found: $MODEL" >&2; echo "Download it from https://huggingface.co/Askit87/Fortera" >&2; exit 2; }
command -v "$BIN" >/dev/null 2>&1 || { echo "llama-server not found in PATH" >&2; exit 2; }
exec "$BIN" --model "$MODEL" --host 127.0.0.1 --port 8081 --ctx-size 4096 --threads "${FORTERA_THREADS:-4}" --parallel 1 --n-gpu-layers 0 --reasoning-format none --no-reasoning-preserve
