"""Build the four-arm inference-perf inputs described in ../DESIGN.md."""

import argparse
import hashlib
from collections import Counter
from copy import deepcopy
from itertools import product
import json
from pathlib import Path
import random
import re
import shutil

import yaml
from tokenizers import Tokenizer

HERE = Path(__file__).resolve().parent
ROUTES = {"amd": 18081, "nvidia": 18082, "ap-nd": 18083, "np-ad": 18084}
SHAPES = [(512, 128), (2048, 256), (2048, 2048), (4096, 512)]


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def work_items(seed):
    phases = {
        "core": [(i, o, c, "burst", "", (), 1)
                 for (i, o), c in product(SHAPES, (1, 2, 4, 8, 16, 24, 32))],
        "arrival": [(i, o, c, "uniform", "", (), 1)
                    for (i, o), c in product(SHAPES, (8, 16))],
        "warm": [(i, o, c, "burst", gpu, (), 1)
                 for (i, o), c, gpu in product(SHAPES[1:], (1, 8, 16), ("amd", "nvidia"))],
        "single-busy": [(i, o, 4, "burst", "", (gpu,), rep)
                        for (i, o), gpu, rep in product(SHAPES[2:], ("amd", "nvidia"), (1, 2))],
        "dual-busy": [(2048, 2048, 4, "burst", "", ("amd", "nvidia"), rep)
                      for rep in (1, 2)],
        "holdout": [(2048, 256, 4, "burst", "amd", (), 1),
                    (3072, 1024, 4, "burst", "", ("nvidia",), 1)],
    }
    preflight = [(512, 8, 1, "burst", "", (), 1),
                 (2048, 256, 1, "burst", "", (), 1),
                 (2048, 2048, 8, "burst", "", (), 1),
                 (2048, 256, 4, "burst", "", ("amd", "nvidia"), 1)]
    rng = random.Random(seed)
    for phase, rows in phases.items():
        rng.shuffle(rows)
    return {"preflight": preflight, **phases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=20261127)
    parser.add_argument("--prefix-block", type=int, default=576)
    parser.add_argument("--background-input", type=int, default=512)
    parser.add_argument("--background-output", type=int, default=2048)
    args = parser.parse_args()
    if not 0 < args.prefix_block < 2048:
        parser.error("--prefix-block must be in [1, 2047]")
    if min(args.background_input, args.background_output) < 1 or (
        args.background_input + args.background_output > 16384
    ):
        parser.error("background lengths must fit within the model context")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    tokenizer = Tokenizer.from_file(str(args.tokenizer / "tokenizer.json"))
    encode = lambda value: tokenizer.encode(value).ids
    words = [w for w in (
        "alpha beta gamma delta red blue green yellow apple banana cherry fish bird bear "
        "tiger road tree train plane house ball sun snow rain stone cloud rope tent cup "
        "gold silver violet sand water oak plum leaf star boat wind day night"
    ).split() if len(encode(" " + w)) == 1]
    if len(words) < 16 or len(encode(" alpha" * 128)) != 128:
        parser.error("tokenizer cannot encode the selected corpus vocabulary")

    # Stage all generated files before renaming so an interrupted build never
    # presents a partial manifest at the requested output path.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staging = args.output.with_name(args.output.name + ".generating")
    staging.mkdir()
    try:
        tokenizer_hashes = {}
        local_tokenizer = staging / "tokenizer"
        local_tokenizer.mkdir()
        for name in ("tokenizer.json", "tokenizer_config.json"):
            source = args.tokenizer / name
            shutil.copyfile(source, local_tokenizer / name)
            tokenizer_hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        for name in ("run-doe.py", "client-pod.yaml"):
            shutil.copyfile(HERE / name, staging / name)

        manifest = {
            "format": 1,
            "model": "Qwen3.5-9B-AWQ",
            "runtime": "9b-v033-rxe",
            "inference_perf_version": "0.7.0",
            "inference_perf_image": "quay.io/inference-perf/inference-perf@sha256:"
                                    "d247e6ad725dc5bb56df71d115abc8ca455bba9f0ccd4110e14bbb56504250ec",
            "seed": args.seed,
            "tokenizer_sha256": tokenizer_hashes,
            "prefix_block_tokens": args.prefix_block,
            "background": {"per_endpoint": 4, "input_tokens": args.background_input,
                           "output_tokens": args.background_output},
            "relay_port": 18771,
            "ports": ROUTES,
            "waves": [],
        }
        arms = tuple(ROUTES)
        templates = {r: yaml.safe_load((HERE / f"{r}.yaml").read_text()) for r in arms}
        positions = {}
        first_blocks = set()
        for phase, rows in work_items(args.seed).items():
            for idx, (i, o, c, arrival, warm, busy, rep) in enumerate(rows):
                ordinal = len(manifest["waves"])
                key = (phase, i, o, c, warm, busy)
                position = positions.setdefault(key, idx % 4)
                rotation = arms[position:] + arms[:position]
                order = rotation if rep == 1 else tuple(reversed(rotation))
                suffix = warm or "-".join(busy) or arrival
                name = f"{phase}-{i}x{o}-c{c}-{suffix}-r{rep}"
                if not re.fullmatch("[a-z0-9-]+", name):
                    raise ValueError(name)
                relative = Path("waves") / name
                folder = staging / relative
                folder.mkdir(parents=True)
                corpus_seed = (
                    f"{args.seed}:independent-holdout:{name}" if phase == "holdout"
                    else f"{args.seed}:{i}:{o}:{c}" if phase in ("core", "arrival")
                    else f"{args.seed}:{name}"
                )
                rng = random.Random(corpus_seed)
                def word_string(count):
                    return "".join(" " + rng.choice(words) for _ in range(count))

                prefix_n = 0
                if warm:
                    prefix_n = min(
                        ((i - 1) // args.prefix_block) * args.prefix_block,
                        round((i * 0.75) / args.prefix_block) * args.prefix_block,
                    )
                prefix = word_string(prefix_n)
                prefix_ids = encode(prefix) if prefix else []
                if len(prefix_ids) != prefix_n:
                    raise ValueError(f"{name}: inconsistent warm prefix length")
                corpus = []
                requests = []
                for index in range(c):
                    prompt = prefix + word_string(i - prefix_n)
                    ids = encode(prompt)
                    word_block = tuple(ids[:min(16, len(ids))])
                    if word_block in first_blocks and not (warm or phase == "arrival"):
                        raise ValueError(f"{name}: duplicate cold leading token block")
                    if len(ids) != i or ids[:prefix_n] != prefix_ids:
                        raise ValueError(f"{name}: invalid token count or prefix boundary")
                    first_blocks.add(word_block)
                    completion = " alpha" * o
                    if index == 0 and len(encode(completion)) != o:
                        raise ValueError(f"{name}: invalid output token count")
                    corpus.append({"conversations": [{"from": "human", "value": prompt},
                                                     {"from": "gpt", "value": completion}]})
                    requests.append({
                        "index": index, "sha256": digest(prompt), "input_tokens": i,
                        "output_tokens": o, "offset_s": index // (c // 4) if arrival == "uniform" else 0,
                    })
                # v0.7.0 consumes one row while initializing the ShareGPT generator.
                write_json(folder / "corpus.json", corpus[-1:] + corpus)

                support = []
                if warm:
                    prompt = prefix + word_string(8)
                    warm_ids = encode(prompt)
                    if len(warm_ids) != prefix_n + 8 or warm_ids[:prefix_n] != prefix_ids:
                        raise ValueError(f"{name}: invalid warmup boundary")
                    support.append({"phase": "warmup", "route": warm, "prompt": prompt,
                                    "input_tokens": len(warm_ids), "output_tokens": 1})
                for gpu in busy:
                    for _ in range(4):
                        prompt = word_string(args.background_input)
                        if len(encode(prompt)) != args.background_input:
                            raise ValueError(f"{name}: invalid background token count")
                        support.append({"phase": "background", "route": gpu, "prompt": prompt,
                                        "input_tokens": args.background_input,
                                        "output_tokens": args.background_output})
                write_json(folder / "support.json", support)

                for route, template in templates.items():
                    config = deepcopy(template)
                    config["data"]["path"] = str(relative / "corpus.json")
                    config["tokenizer"]["pretrained_model_name_or_path"] = "./tokenizer"
                    config["load"]["stages"] = [{"num_requests": c, "concurrency_level": c}]
                    config["load"]["worker_max_tcp_connections"] = max(32, c)
                    config["load"]["base_seed"] = args.seed
                    config["server"]["base_url"] = f"http://127.0.0.1:{manifest['relay_port']}"
                    config["storage"]["local_storage"]["path"] = str(Path("results") / name / route / "inference-perf")
                    (folder / f"{route}.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
                manifest["waves"].append({
                    "name": name, "phase": phase, "ordinal": ordinal,
                    "directory": str(relative), "arms": list(order),
                    "input_tokens": i, "output_tokens": o, "concurrency": c,
                    "arrival": arrival, "warm_endpoint": warm, "warm_prefix_tokens": prefix_n,
                    "busy_endpoints": busy, "repeat": rep, "requests": requests,
                    "corpus_sha256": hashlib.sha256((folder / "corpus.json").read_bytes()).hexdigest(),
                    "support_sha256": hashlib.sha256((folder / "support.json").read_bytes()).hexdigest(),
                })
        counts = Counter()
        groups = Counter()
        for wave in manifest["waves"]:
            counts[wave["phase"]] += wave["concurrency"] * len(ROUTES)
            groups[wave["phase"]] += 1
        manifest["target_requests_by_phase"] = dict(counts)
        manifest["blocks_by_phase"] = dict(groups)
        write_json(staging / "manifest.json", manifest)
        staging.rename(args.output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f"Prepared {len(manifest['waves'])} blocks: {dict(counts)}")


if __name__ == "__main__":
    main()
