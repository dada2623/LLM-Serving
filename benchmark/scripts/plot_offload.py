"""Plot the CPU-offload ablation from processed/offload-ablation.json."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
data = json.loads((ROOT / "benchmark" / "processed" / "offload-ablation.json").read_text(encoding="utf-8"))
arms = sorted(data["arms"], key=lambda arm: arm["cpu_offload_gb_arg"])
labels = [f'{arm["cpu_offload_gb_arg"]:g} GiB arg\n({arm.get("offloaded_declared_gib") or 0:.2f} GiB actual)' for arm in arms]
colors = ["#1e8449", "#d68910", "#b03a2e"]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.4), constrained_layout=True)

decode = [arm["median_decode_chunk_tokens_per_s"] for arm in arms]
bars = ax1.bar(range(len(arms)), decode, color=colors, width=0.6)
ax1.set_xticks(range(len(arms)), labels, fontsize=9)
ax1.set_ylabel("decode speed (tokens/s)")
ax1.set_title("Decode throughput vs CPU-offload budget")
ax1.grid(axis="y", alpha=0.25)
for bar, value in zip(bars, decode):
    ax1.text(bar.get_x() + bar.get_width() / 2, value + 0.6, f"{value:.2f}", ha="center", va="bottom", fontsize=10)
ax1.set_ylim(0, 36)
ax1.annotate("", xy=(0, 33), xytext=(2, 33),
             arrowprops=dict(arrowstyle="<->", color="black"))
ax1.text(1, 33.5, f"{max(decode) / min(decode):.2f}x", ha="center", va="bottom",
         fontsize=11, fontweight="bold")

for arm, color in zip(arms, colors):
    points = sorted(arm["requests"], key=lambda row: row["usage"]["prompt_tokens"])
    x = [row["usage"]["prompt_tokens"] for row in points]
    y = [row["e2e_ms"] / 1000 for row in points]
    ax2.plot(x, y, marker="o", color=color, label=f'{arm["cpu_offload_gb_arg"]:g} GiB arg')
ax2.set_xscale("log", base=2)
ax2.set_xticks([128, 512, 2048], ["128", "512", "2048"])
ax2.set_xlabel("input tokens (output fixed at 32)")
ax2.set_ylabel("end-to-end latency (s)")
ax2.set_title("End-to-end latency vs prompt length")
ax2.grid(alpha=0.25)
ax2.legend(fontsize=9)

fig.suptitle("Qwen3.5-9B-AWQ on RTX 5070 Ti: removing CPU offload removes the per-token PCIe copy path", fontsize=11)
fig.text(0.5, -0.03, "Single request per length, temperature 0, max-num-seqs 1, 16K context, WSL 2 + Docker Desktop.",
         ha="center", fontsize=7.5, color="0.35")

out = ROOT / "benchmark" / "figures"
out.mkdir(exist_ok=True)
fig.savefig(out / "offload-ablation.png", dpi=180, bbox_inches="tight")
fig.savefig(out / "offload-ablation.svg", bbox_inches="tight", metadata={"Date": None})
print(out / "offload-ablation.png")
