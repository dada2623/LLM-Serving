"""Run the generated inference-perf waves with a local SSE relay."""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import signal
import time
import uuid

import aiohttp
from aiohttp import web

METRIC = re.compile(r"^vllm:num_requests_(running|waiting)(?:\{[^}]*\})?\s+(\S+)", re.M)
ROLES = ("amd", "nvidia")


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


async def queue_state(session, metrics_urls):
    async def scrape(url):
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as response:
            response.raise_for_status()
            rows = METRIC.findall(await response.text())
        totals = Counter()
        for name, value in rows:
            totals[name] += float(value)
        if not {"running", "waiting"} <= totals.keys():
            raise RuntimeError(f"running/waiting gauges missing at {url}")
        return dict(totals)
    states = await asyncio.gather(*(scrape(metrics_urls[role]) for role in ROLES))
    return {"time": time.time(), **dict(zip(ROLES, states))}


async def wait_idle(session, metrics_urls):
    deadline = time.monotonic() + 90
    while True:
        state = await queue_state(session, metrics_urls)
        if all(state[role] == {"running": 0, "waiting": 0} for role in ROLES):
            return state
        if time.monotonic() >= deadline:
            raise TimeoutError(f"workers did not return to idle: {state}")
        await asyncio.sleep(0.25)


async def wait_for(predicate, process, timeout, description, failed=lambda: False):
    deadline = time.monotonic() + timeout
    while not predicate():
        if failed():
            raise RuntimeError(f"{description}: a prerequisite stream ended")
        if process.returncode is not None:
            raise RuntimeError(f"{description}: inference-perf exited ({process.returncode})")
        if time.monotonic() >= deadline:
            raise TimeoutError(description)
        await asyncio.sleep(0.005)


async def run_arm(args, root, manifest, wave, route, session, upstream, metrics_urls):
    out = root / "results" / wave["name"] / route
    out.mkdir(parents=True, exist_ok=True)
    if not args.rerun and (out / "arm.json").is_file():
        if read(out / "arm.json")["status"] == "completed":
            print(f"resume {wave['name']} {route}", flush=True)
            return
    # Keep prior attempt evidence and let the YAML keep its stable result path.
    prior_files = [path for path in out.iterdir() if path.name != "attempts"]
    if prior_files:
        backup = out / "attempts" / uuid.uuid4().hex
        backup.mkdir(parents=True)
        for path in prior_files:
            path.rename(backup / path.name)

    salt = uuid.uuid4().hex
    expected = {row["sha256"]: row for row in wave["requests"]}
    target = {}
    aux = []
    ready = {}
    released_at = None
    queue = []
    process = None
    aux_tasks = []
    started_utc = utc_now()
    work = root / wave["directory"]
    for file in ("corpus", "support"):
        actual = hashlib.sha256((work / f"{file}.json").read_bytes()).hexdigest()
        if actual != wave[f"{file}_sha256"]:
            raise RuntimeError(f"{work}: {file} checksum mismatch")

    async def forward(payload, destination, row, request=None):
        payload["cache_salt"] = salt
        headers = {"x-request-id": str(uuid.uuid4())}
        row["forwarded"] = time.time()
        row["outbound_request_id"] = headers["x-request-id"]
        stream = None
        try:
            async with session.post(upstream[destination] + "/v1/completions",
                                    json=payload, headers=headers) as reply:
                row["http_status"] = reply.status
                row["envoy_request_id"] = reply.headers.get("x-request-id")
                row["executed_pd"] = reply.headers.get("x-epp-opt-executed-pd")
                if reply.status != 200:
                    error = await reply.read()
                    row["error"] = error.decode(errors="replace")[:1000]
                    return web.Response(status=reply.status, body=error)
                if request is not None:
                    response_headers = {"Content-Type": "text/event-stream"}
                    if row["envoy_request_id"]:
                        response_headers["x-request-id"] = row["envoy_request_id"]
                    stream = web.StreamResponse(status=200, headers=response_headers)
                    await stream.prepare(request)
                async for line in reply.content:
                    if line.strip() == b"data: [DONE]":
                        row["done"] = True
                        row["done_at"] = time.time()
                    elif line.startswith(b"data: "):
                        item = json.loads(line[6:])
                        if item.get("usage"):
                            row["usage"] = item["usage"]
                        if "first_token" not in row and any(c.get("text") for c in item.get("choices", [])):
                            row["first_token"] = time.time()
                    if stream is not None:
                        await stream.write(line)
                if stream is not None:
                    await stream.write_eof()
                return stream
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as error:
            row["error"] = repr(error)
            if stream is not None:
                return stream
            return web.Response(status=502, text="upstream stream failed")
        finally:
            row["ended"] = time.time()

    async def relay(request):
        payload = await request.json()
        prompt = payload.get("prompt")
        key = hashlib.sha256(prompt.encode()).hexdigest() if isinstance(prompt, str) else ""
        want = expected.get(key)
        if (want is None or key in target or payload.get("model") != manifest["model"]
                or payload.get("max_tokens") != want["output_tokens"]
                or payload.get("stream") is not True or payload.get("ignore_eos") is not True
                or payload.get("stream_options", {}).get("include_usage") is not True):
            return web.Response(status=400, text="unexpected or repeated inference-perf request")
        row = target[key] = {**want, "route": route, "parked": time.time()}
        event = ready[key] = asyncio.Event()
        await event.wait()
        return await forward(payload, route, row, request)

    async def support(entry):
        row = {k: v for k, v in entry.items() if k != "prompt"}
        aux.append(row)
        payload = {"model": manifest["model"], "prompt": entry["prompt"],
                   "max_tokens": entry["output_tokens"], "ignore_eos": True,
                   "stream": True, "stream_options": {"include_usage": True}}
        await forward(payload, entry["route"], row)
        return row

    async def record_queue():
        while True:
            try:
                queue.append(await queue_state(session, metrics_urls))
            except (aiohttp.ClientError, RuntimeError, TimeoutError, ValueError) as error:
                queue.append({"time": time.time(), "error": repr(error)})
            await asyncio.sleep(0.15)

    def check_row(row, marker_required=False):
        usage = row.get("usage", {})
        if not (row.get("done") and row.get("first_token") and row.get("http_status") == 200
                and row.get("envoy_request_id")
                and usage.get("prompt_tokens") == row["input_tokens"]
                and usage.get("completion_tokens") == row["output_tokens"]
                and bool(row.get("executed_pd")) == marker_required):
            raise RuntimeError(f"failed response evidence: {row}")

    app = web.Application()
    app.router.add_post("/v1/completions", relay)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    polling = None
    status = "failed"
    error_text = None
    try:
        site = web.TCPSite(runner, "127.0.0.1", manifest["relay_port"], backlog=128)
        await site.start()
        queue.append(await wait_idle(session, metrics_urls))
        with (out / "inference-perf.log").open("w") as log:
            process = await asyncio.create_subprocess_exec(
                args.inference_perf, "--config_file", str(Path(wave["directory"]) / f"{route}.yaml"),
                cwd=root, stdout=log, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            )
        await wait_for(lambda: ready.keys() == expected.keys(), process, 90,
                       "waiting for all target requests at the relay")
        polling = asyncio.create_task(record_queue())
        entries = read(work / "support.json")
        for entry in filter(lambda item: item["phase"] == "warmup", entries):
            check_row(await support(entry))
            queue.append(await wait_idle(session, metrics_urls))
        background = [entry for entry in entries if entry["phase"] == "background"]
        if background:
            aux_tasks = [asyncio.create_task(support(entry)) for entry in background]
            def ended():
                return any("done_at" in row or "ended" in row
                           for row in aux if row["phase"] == "background")
            await wait_for(
                lambda: len([r for r in aux if r["phase"] == "background" and "first_token" in r])
                == len(background) and not ended(),
                process, 60, "waiting for all background streams to enter Decode", ended,
            )
        released_at = time.time()
        loop = asyncio.get_running_loop()
        start = loop.time()
        for key, want in expected.items():
            loop.call_at(start + want["offset_s"], ready[key].set)
        await asyncio.wait_for(process.wait(), 260)
        if process.returncode:
            raise RuntimeError(f"inference-perf exited ({process.returncode})")
        for task in aux_tasks:
            check_row(await task)
        for row in target.values():
            check_row(row, route in ("ap-nd", "np-ad"))
        dispatch_error = max(r["forwarded"] - released_at - r["offset_s"] for r in target.values())
        if dispatch_error > 0.100:
            raise RuntimeError(f"target dispatch exceeded planned offset by {dispatch_error:.3f}s")
        last_send = max(r["forwarded"] for r in target.values())
        if background:
            ended_at = min(row.get("done_at", row["ended"]) for row in aux
                           if row["phase"] == "background")
            if last_send >= ended_at:
                raise RuntimeError("background Decode did not cover target dispatch")
        ids = [r["envoy_request_id"] for r in target.values()]
        if len(ids) != len(set(ids)):
            raise RuntimeError("duplicate Envoy request ID")
        (out / "target-request-ids.txt").write_text("".join(value + "\n" for value in ids))
        status = "completed"
        print(f"{wave['name']} {route} {len(ids)}/{len(expected)}", flush=True)
    except BaseException as error:
        error_text = repr(error)
        raise
    finally:
        if process is not None and status != "completed":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        for task in aux_tasks + ([polling] if polling else []):
            task.cancel()
        await asyncio.gather(*aux_tasks, *([polling] if polling else []), return_exceptions=True)
        await runner.cleanup()
        save(out / "arm.json", {"status": status, "error": error_text, "start_utc": started_utc,
                               "released_at": released_at, "route": route, "target": list(target.values()),
                               "support": aux, "queue": queue})


async def run(args):
    root = args.bundle.resolve()
    manifest = read(root / "manifest.json")
    if not args.test_client and version("inference-perf") != manifest["inference_perf_version"]:
        raise RuntimeError(f"expected inference-perf {manifest['inference_perf_version']}")
    waves = [w for w in manifest["waves"] if args.phase in ("all", w["phase"])
             and (not args.wave or args.wave == w["name"])]
    if not waves:
        raise RuntimeError("no matching wave")
    upstream = {r: f"http://{args.service}:{p}" for r, p in manifest["ports"].items()}
    metrics_urls = {"amd": args.amd_metrics, "nvidia": args.nvidia_metrics}
    connector = aiohttp.TCPConnector(limit=64)
    async with aiohttp.ClientSession(connector=connector,
                                     timeout=aiohttp.ClientTimeout(total=245)) as session:
        for wave in waves:
            for route in wave["arms"]:
                if args.route is None or args.route == route:
                    await run_arm(args, root, manifest, wave, route, session, upstream, metrics_urls)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--phase", default="preflight",
                        choices=("preflight", "core", "arrival", "warm", "single-busy", "dual-busy", "holdout", "all"))
    parser.add_argument("--wave")
    parser.add_argument("--route", choices=("amd", "nvidia", "ap-nd", "np-ad"))
    parser.add_argument("--rerun", action="store_true", help="replace an already completed arm")
    parser.add_argument("--service", default="epp-opt-calibration.llm-d-serving.svc.cluster.local")
    parser.add_argument("--amd-metrics", required=True)
    parser.add_argument("--nvidia-metrics", required=True)
    parser.add_argument("--inference-perf", default="inference-perf")
    parser.add_argument("--test-client", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.rerun and not (args.wave and args.route):
        parser.error("--rerun requires both --wave and --route")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
