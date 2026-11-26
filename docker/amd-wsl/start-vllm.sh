#!/usr/bin/env bash
set -euo pipefail
model=/models/Qwen3.5-9B-AWQ
if [[ ! -f "$model/config.json" ]]; then
    printf 'Model is not installed at %s. Install the selected QuantTrio checkpoint before starting inference.\n' "$model" >&2
    exit 66
fi
exec vllm serve "$model" \
    --host 0.0.0.0 --port 8000 \
    --served-model-name Qwen3.5-9B-AWQ \
    --dtype float16 --quantization awq \
    --max-model-len 16384 --max-num-seqs 1 \
    --max-num-batched-tokens 512 \
    --gpu-memory-utilization 0.80 \
    --enforce-eager \
    --language-model-only \
    --attention-config '{"backend":"TRITON_ATTN"}' \
    --reasoning-parser qwen3 \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder
