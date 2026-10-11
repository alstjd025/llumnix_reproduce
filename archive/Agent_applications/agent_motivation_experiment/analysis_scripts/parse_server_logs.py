#!/usr/bin/env python3
"""Parse SGLang server stderr logs into a normalized CSV."""

import argparse
import glob
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd


DECODE_PATTERN = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \w+\] "
    r"Decode batch, #running-req: (\d+), #token: (\d+), "
    r"token usage: ([\d.]+),.*"
    r"gen throughput \(token/s\): ([\d.]+), #queue-req: (\d+)"
)
PREFILL_PATTERN = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \w+\] "
    r"Prefill batch, #new-seq: (\d+), #new-token: (\d+), #cached-token: (\d+), "
    r"token usage: ([\d.]+), #running-req: (\d+), #queue-req: (\d+),.*"
    r"input throughput \(token/s\): ([\d.]+)"
)
REQ_PATTERN = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \w+\] "
    r"Req Time Stats\(rid=[^,]+, input len=(\d+), output len=(\d+), type=\w+\): "
    r"queue_duration=([\d.]+)ms, forward_duration=([\d.]+)ms"
)


def _load_config(result_dir: str) -> dict:
    path = Path(result_dir) / "run_config.json"
    if path.is_file():
        with open(path) as f:
            return json.load(f)
    return {}


def _experiment_label(config: dict, result_dir: str) -> str:
    if config.get("lambda") is not None:
        return f"lambda={config['lambda']}"
    if config.get("request_rate_per_min") is not None:
        return f"rpm={config['request_rate_per_min']}"
    if config.get("concurrency") is not None:
        return f"concurrency={config['concurrency']}"
    return Path(result_dir).name


def _client_t0(result_dir: str) -> float | None:
    metrics_csv = Path(result_dir) / "metrics.csv"
    if not metrics_csv.is_file():
        return None
    df = pd.read_csv(metrics_csv)
    calls = df[df["agent"].astype(str).str.startswith("chain_call")]
    if calls.empty:
        return None
    return float(pd.to_numeric(calls["start_time"], errors="coerce").min())


def parse_result_dir(result_dir: str) -> pd.DataFrame:
    config = _load_config(result_dir)
    label = _experiment_label(config, result_dir)
    t0 = _client_t0(result_dir)
    rows = []

    for log_path in sorted(glob.glob(os.path.join(result_dir, "server.stderr*"))):
        with open(log_path) as f:
            for line in f:
                row = None
                if "Prefill batch" in line:
                    match = PREFILL_PATTERN.search(line)
                    if match:
                        row = {
                            "event_type": "prefill",
                            "new_seq": int(match.group(2)),
                            "new_token": int(match.group(3)),
                            "cached_token": int(match.group(4)),
                            "token_usage": float(match.group(5)),
                            "running_req": int(match.group(6)),
                            "queue_req": int(match.group(7)),
                            "prefill_throughput": float(match.group(8)),
                        }
                elif "Decode batch" in line:
                    match = DECODE_PATTERN.search(line)
                    if match:
                        row = {
                            "event_type": "decode",
                            "running_req": int(match.group(2)),
                            "decode_tokens": int(match.group(3)),
                            "token_usage": float(match.group(4)),
                            "gen_throughput": float(match.group(5)),
                            "queue_req": int(match.group(6)),
                        }
                elif "Req Time Stats" in line:
                    match = REQ_PATTERN.search(line)
                    if match:
                        row = {
                            "event_type": "req_stats",
                            "input_len": int(match.group(2)),
                            "output_len": int(match.group(3)),
                            "queue_duration_ms": float(match.group(4)),
                            "forward_duration_ms": float(match.group(5)),
                        }

                if row is None:
                    continue

                ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                epoch = int(ts.timestamp())
                row.update({
                    "result_dir": result_dir,
                    "experiment": label,
                    "mode": config.get("mode"),
                    "lambda": config.get("lambda"),
                    "request_rate_per_min": config.get("request_rate_per_min"),
                    "concurrency": config.get("concurrency"),
                    "epoch": epoch,
                    "rel_time": epoch - t0 if t0 is not None else None,
                    "source_log": os.path.basename(log_path),
                })
                rows.append(row)

    return pd.DataFrame(rows)


def parse_server_logs(result_dirs: list[str]) -> pd.DataFrame:
    frames = [parse_result_dir(result_dir) for result_dir in result_dirs]
    frames = [df for df in frames if not df.empty]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dirs", nargs="+", help="Experiment result directories")
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Output CSV path. Defaults to <first_result_dir>/analysis/server_metrics.csv.",
    )
    args = parser.parse_args()

    df = parse_server_logs(args.result_dirs)
    if args.output_csv:
        output_csv = args.output_csv
    else:
        output_dir = Path(args.result_dirs[0]) / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_csv = output_dir / "server_metrics.csv"
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    print(f"rows: {len(df)}")
    print(f"server_metrics_csv: {output_csv}")


if __name__ == "__main__":
    main()
