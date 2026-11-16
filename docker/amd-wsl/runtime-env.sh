#!/usr/bin/env bash
# Source this file for the validated SDK 10 / ROCDXG 1.2.2 environment.
venv=/opt/llm-serving/venvs/vllm-rocm100-v033
export PATH="$venv/bin:$PATH"
export HSA_ENABLE_DXG_DETECTION=1 ROCR_VISIBLE_DEVICES=SET_ONE_VISIBLE_GPU
export ROCPROFILER_REGISTER_ENABLED=0 LLM_SERVING_WSL_ROCM=1
export VLLM_PLUGINS=llm_serving_wsl_rocm VLLM_USE_TRITON_AWQ=1 VLLM_ROCM_USE_AITER=0
export LD_LIBRARY_PATH="/opt/llm-serving/rocm100-wsl-host-bridge:$venv/lib/python3.12/site-packages/_rocm_sdk_core/lib:$venv/lib/python3.12/site-packages/_rocm_sdk_libraries/lib:/usr/lib/wsl/lib"
export ROCM_PATH="$venv/lib/python3.12/site-packages/_rocm_sdk_devel"
export ROCM_HOME="$ROCM_PATH" HIP_PATH="$ROCM_PATH"
export HF_HUB_OFFLINE=1 HF_HOME=/opt/llm-serving/cache-v033/huggingface
export VLLM_CACHE_ROOT=/opt/llm-serving/cache-v033/vllm
export TRITON_CACHE_DIR=/opt/llm-serving/cache-v033/triton
