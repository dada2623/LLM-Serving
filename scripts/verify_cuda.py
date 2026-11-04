"""Verify GPU execution and numerical results on the local CUDA environment."""

import json
import platform

import torch


def main() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable. Check the NVIDIA driver and PyTorch build.")

    device = torch.device("cuda:0")
    properties = torch.cuda.get_device_properties(device)
    torch.manual_seed(42)
    left = torch.randn(256, 256, dtype=torch.float32)
    right = torch.randn(256, 256, dtype=torch.float32)
    expected = left @ right
    actual = left.to(device) @ right.to(device)
    torch.cuda.synchronize()
    torch.testing.assert_close(actual.cpu(), expected, rtol=1e-4, atol=1e-4)
    torch.testing.assert_close(torch.from_numpy(left.numpy()), left)

    # Exercise the lower precision kernels typically used for LLM inference.
    with torch.inference_mode():
        low_precision = left.to(device, dtype=torch.bfloat16) @ right.to(device, dtype=torch.bfloat16)
        torch.cuda.synchronize()
        if not torch.isfinite(low_precision).all().item():
            raise RuntimeError("BF16 GPU matrix multiplication returned non-finite results.")

    print(json.dumps({
        "python": platform.python_version(),
        "pytorch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "gpu": properties.name,
        "gpu_memory_gib": round(properties.total_memory / 1024**3, 2),
        "compute_capability": list(torch.cuda.get_device_capability(device)),
        "supported_architectures": torch.cuda.get_arch_list(),
        "fp32_matmul": "passed",
        "bf16_matmul": "passed",
        "numpy_interop": "passed",
    }, indent=2))


if __name__ == "__main__":
    main()
