ARG BASE_IMAGE=vllm/vllm-openai:v0.33.0
FROM ${BASE_IMAGE}

ENV HF_HOME=/root/.cache/huggingface PYTHONUNBUFFERED=1
COPY docker/gpu_probe.py /opt/llm/gpu_probe.py
COPY docker/api_probe.py /opt/llm/api_probe.py

# The official image supplies vLLM, CUDA libraries and its vllm serve entrypoint.
