"""Sample nvidia-smi into a CSV file using only the Python standard library."""

import argparse
import csv
from datetime import datetime
import io
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time


QUERY_FIELDS = (
    "timestamp", "index", "uuid", "name", "memory.used", "memory.free",
    "memory.total", "utilization.gpu", "utilization.memory",
    "temperature.gpu", "power.draw",
)
CSV_FIELDS = (
    "timestamp", "elapsed_seconds", "gpu_index", "gpu_uuid", "gpu_name",
    "memory_used_mib", "memory_free_mib", "memory_total_mib",
    "memory_used_percent", "gpu_utilization_percent", "memory_activity_percent",
    "temperature_c", "power_w",
)


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def numeric(value: str) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def sample(executable: str, gpu: str | None) -> list[list[str]]:
    command = [executable, f"--query-gpu={','.join(QUERY_FIELDS)}",
               "--format=csv,noheader,nounits"]
    if gpu is not None:
        command.append(f"--id={gpu}")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"nvidia-smi failed ({result.returncode}): {message}")
    rows = list(csv.reader(io.StringIO(result.stdout), skipinitialspace=True))
    if not rows or any(len(row) != len(QUERY_FIELDS) for row in rows):
        raise RuntimeError("nvidia-smi returned an empty or unexpected CSV response")
    return [[value.strip() for value in row] for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=positive_seconds, default=1.0,
                        help="seconds between samples (default: 1)")
    parser.add_argument("--duration", type=positive_seconds,
                        help="stop after this many seconds; otherwise use Ctrl+C")
    parser.add_argument("--gpu", help="GPU index or UUID; default: all GPUs")
    parser.add_argument("--output", type=Path, help="new CSV path; existing files are preserved")
    parser.add_argument("--quiet", action="store_true", help="hide per-sample console output")
    args = parser.parse_args()
    executable = shutil.which("nvidia-smi")
    if executable is None:
        parser.error("nvidia-smi was not found on PATH; check the NVIDIA driver")

    output = args.output or (
        Path(__file__).resolve().parents[1] / "outputs" /
        f"gpu_metrics_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
    )
    output = output.resolve()
    row_count = 0
    peaks: dict[str, float] = {}
    exit_code = 0
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects earlier monitoring results from overwrites.
        with output.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(CSV_FIELDS)
            stream.flush()
            print(f"CSV: {output}", flush=True)
            print(f"Interval: {args.interval:g}s. Press Ctrl+C to stop.", flush=True)
            started = time.monotonic()
            next_sample = started
            while args.duration is None or time.monotonic() - started < args.duration:
                elapsed = time.monotonic() - started
                for row in sample(executable, args.gpu):
                    stamp, index, uuid, name, used, free, total, util, activity, temp, power = row
                    used_value, total_value = numeric(used), numeric(total)
                    percent = (
                        round(100 * used_value / total_value, 2)
                        if used_value is not None and total_value is not None and total_value > 0
                        else ""
                    )
                    writer.writerow([
                        stamp, round(elapsed, 3), index, uuid, name, used, free, total,
                        percent, util, activity, temp, power,
                    ])
                    row_count += 1
                    if used_value is not None:
                        label = f"GPU {index} ({uuid})"
                        peaks[label] = max(peaks.get(label, 0), used_value)
                    if not args.quiet:
                        print(
                            f"{stamp} | GPU {index} | VRAM {used}/{total} MiB "
                            f"({percent if percent != '' else 'N/A'}%) | "
                            f"GPU {util}% | {temp} C | {power} W", flush=True,
                        )
                # Flush each snapshot so other tools can read a live CSV.
                stream.flush()
                now = time.monotonic()
                next_sample += args.interval
                if next_sample < now:
                    # Skip missed intervals instead of issuing catch-up queries.
                    next_sample = started + (math.floor((now - started) / args.interval) + 1) * args.interval
                delay = max(0.0, next_sample - now)
                if args.duration is not None:
                    delay = min(delay, max(0.0, args.duration - (now - started)))
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Error: {error}", file=sys.stderr)
        exit_code = 1

    print(f"Saved {row_count} rows. CSV: {output}")
    for label, peak in peaks.items():
        print(f"{label}: sampled peak VRAM = {peak:g} MiB")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
