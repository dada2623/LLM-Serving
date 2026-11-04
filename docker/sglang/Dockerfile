ARG BASE_IMAGE=lmsysorg/sglang:v0.5.22
FROM ${BASE_IMAGE}

ENV HF_HOME=/root/.cache/huggingface PYTHONUNBUFFERED=1
COPY docker/gpu_probe.py /opt/llm/gpu_probe.py
COPY docker/api_probe.py /opt/llm/api_probe.py
COPY docker/sglang/patch_offload.py /opt/llm/patch_offload.py
RUN python3 /opt/llm/patch_offload.py

ENTRYPOINT ["python3", "-m", "sglang.launch_server"]
