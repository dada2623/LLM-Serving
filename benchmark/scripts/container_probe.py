"""Read-only metadata/tokenizer probe executed inside each installed image."""
import argparse
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import platform
import sys
import time


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--dataset", action="store_true")
    args = parser.parse_args()
    import torch
    from transformers import AutoTokenizer

    model = Path("/models/Qwen3.5-9B-AWQ")
    tokenizer = AutoTokenizer.from_pretrained(str(model), local_files_only=True)
    packages = {}
    for name in ("vllm", "sglang", "torch", "transformers", "tokenizers", "flashinfer-python", "triton", "sgl-kernel"):
        try:
            packages[name] = md.version(name)
        except md.PackageNotFoundError:
            packages[name] = None
    result = {
        "service": args.service, "python": sys.version, "platform": platform.platform(),
        "packages": packages, "torch_cuda_build": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "container_utc_epoch_ns": time.time_ns(),
        "tokenizer_class": type(tokenizer).__name__, "tokenizer_size": len(tokenizer),
        "tokenizer_files_sha256": {
            name: digest((model / name).read_bytes())
            for name in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "vocab.json", "merges.txt")
        },
    }
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        result["gpu"] = {"name": props.name, "total_memory_bytes": props.total_memory,
                         "compute_capability": list(torch.cuda.get_device_capability(0)),
                         "bf16_supported": torch.cuda.is_bf16_supported()}
    if args.dataset:
        # These are chat-template token counts, including generation markers.
        cases = []
        for index, target in enumerate((128, 512, 2048)):
            namespace = hashlib.sha256(f"20261030/{index}".encode()).hexdigest()[:16]
            start = f"{namespace}\nSynthetic calibration notes:\n"
            end = "\nTask: Write a numbered list of 100 practical tips for learning Python. Begin immediately."
            filler = "Measurements use local synthetic data. Record latency, memory, and power. "
            raw = tokenizer.encode(start + filler * (target // 8 + 8), add_special_tokens=False)
            selected = None
            observed = []
            for length in range(1, target + 30):
                content = tokenizer.decode(raw[:length], skip_special_tokens=False) + end
                messages = [{"role": "user", "content": content}]
                ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, enable_thinking=False)
                if hasattr(ids, "keys"):
                    ids = ids["input_ids"]
                observed.append(len(ids))
                if len(ids) == target:
                    selected = (messages, ids)
                    break
            if selected is None:
                raise RuntimeError(f"Could not construct exactly {target} chat tokens: range {min(observed)}..{max(observed)}, nearby {sorted(set(observed), key=lambda n: abs(n-target))[:12]}")
            messages, ids = selected
            cases.append({"case_id": f"p0-input-{target}", "target_input_tokens": target,
                          "expected_input_tokens": len(ids), "max_output_tokens": 32,
                          "messages": messages, "input_ids": ids,
                          "input_ids_sha256": digest(json.dumps(ids, separators=(",", ":")).encode())})
        result["dataset"] = {"version": 1, "seed": 20261030,
                             "thinking": False, "temperature": 0, "top_p": 1,
                             "template_options": {"add_generation_prompt": True, "enable_thinking": False},
                             "cases": cases}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
