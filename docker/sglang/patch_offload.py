"""Add an opt-in MLP-only selector to SGLang v0.5.22's V1 CPU offloader.

Qwen3.5 GemmaRMSNorm's loader writes a CUDA gemma_weight buffer directly.
Moving its parameter to CPU before loading breaks that operation. GDN also
retains device-side state. Offload only each decoder layer's MLP, keeping the
original offloader's transfer and functional_call behavior for that MLP.
"""

from pathlib import Path


source = Path("/sgl-workspace/sglang/python/sglang/srt/utils/offloader.py")
original = "        return [self.maybe_offload_to_cpu(module) for module in all_modules_generator]"
replacement = '''        if os.environ.get("SGLANG_CPU_OFFLOAD_MLP_ONLY") == "1":
            modules = []
            for module in all_modules_generator:
                mlp = getattr(module, "mlp", None)
                if not isinstance(mlp, torch.nn.Module):
                    raise RuntimeError(
                        "SGLANG_CPU_OFFLOAD_MLP_ONLY requires decoder layers with an mlp module"
                    )
                self.maybe_offload_to_cpu(mlp)
                modules.append(module)
            logger.info(
                "MLP-only CPU offload: %.3f GiB; attention and norms stay on GPU",
                self._cpu_offload_bytes / 1024**3,
            )
            return modules
        return [self.maybe_offload_to_cpu(module) for module in all_modules_generator]'''
text = source.read_text()
if text.count(original) != 1:
    raise RuntimeError("SGLang offloader changed; review MLP-only compatibility patch")
source.write_text(text.replace(original, replacement))
print("Applied opt-in MLP-only CPU offload compatibility patch")
