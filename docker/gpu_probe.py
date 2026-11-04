"""Check injected NVIDIA utilities and execute CUDA kernels inside a container."""

import json
import os
import shutil
import subprocess

import torch


def main() -> None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        raise RuntimeError("nvidia-smi is missing: check Docker GPU device requests")
    result = subprocess.run(
        [executable, "--query-gpu=name,uuid,memory.total", "--format=csv,noheader"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is false inside the container")
    torch.manual_seed(42)
    source = torch.randn(128, 128)
    expected = source @ source.T
    on_gpu = source.cuda()
    actual = on_gpu @ on_gpu.T
    torch.cuda.synchronize()
    torch.testing.assert_close(actual.cpu(), expected, rtol=1e-4, atol=1e-4)
    bf16 = on_gpu.to(torch.bfloat16)
    low_precision = bf16 @ bf16.T
    torch.cuda.synchronize()
    if not torch.isfinite(low_precision).all().item():
        raise RuntimeError("BF16 GPU matrix multiplication produced non-finite values")
    print(json.dumps({
        "nvidia_smi": result.stdout.strip(),
        "nvidia_visible_devices": os.getenv("NVIDIA_VISIBLE_DEVICES"),
        "pytorch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "cuda_available": True,
        "gpu": torch.cuda.get_device_name(0),
        "compute_capability": torch.cuda.get_device_capability(0),
        "supported_architectures": torch.cuda.get_arch_list(),
        "fp32_matmul": "passed",
        "bf16_matmul": "passed",
    }, indent=2))


if __name__ == "__main__":
    main()
