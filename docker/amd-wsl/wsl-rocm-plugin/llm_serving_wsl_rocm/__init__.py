"""Opt-in adapter for the single GPU exposed through WSL DXG.

vLLM's Linux ROCm detection requires AMDSMI. WSL can expose HIP through
DXG while AMDSMI is unavailable. Use HIP for discovery only on this
explicitly enabled, single-GPU WSL deployment. GPU kernels stay upstream.
"""
import os
from pathlib import Path


def register():
    if os.environ.get("LLM_SERVING_WSL_ROCM") != "1":
        return None
    if not Path("/dev/dxg").exists():
        return None
    import torch
    if not torch.version.hip or not torch.cuda.is_available():
        return None
    if torch.cuda.device_count() != 1:
        raise RuntimeError("This WSL adapter requires exactly one visible GPU")
    return "llm_serving_wsl_rocm.platform.WslRocmPlatform"
