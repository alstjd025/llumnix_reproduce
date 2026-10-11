#!/usr/bin/env python3
"""Join client request-ids with scheduler dispatch logs -> per-request engine.

Produces `analysis/request_engine.csv` for a run:

    task_id, call_index, request_id, uuid, instance_id, engine_port, migrated

Inputs (both produced automatically by an `--engine llumnix` run):
  * `request_ids.jsonl`            - client sidecar: task_id -> server request id
                                     ("cmpl-<uuid>"), written by
                                     invoke_with_tracking.
  * `server_metrics/scheduler_dispatch.log` - scheduler lines captured live by
                                     LlumnixMetricsCollector:
       "[Schedule] dispatch request <uuid> to neutral instance <instance_id>"
       "[refreshInstanceMetadata] instanceID=<id>, metadata=... api_server_port:80xx"

The client id is "cmpl-<uuid>" (streaming) or "chatcmpl-<uuid>"; the scheduler
logs the bare <uuid>, so we normalise by stripping the prefix (and any trailing
"-0" the engine appends).

Attribution semantics: the dispatch line records the request's INITIAL
placement. Llumnix may migrate a request between instances afterwards, so rows
whose uuid appears in a migration line are flagged `migrated=True` — treat
those as "first engine" rather than "the engine that did all the work", and
filter them out for strict per-engine analyses.

See experiments/DEV_request-engine-attribution.md.
"""
import argparse
import csv
import json
import os
import re
import sys

DISPATCH_RE = re.compile(
    r"\[Schedule\] dispatch request ([0-9a-fA-F-]{8,}) to \S+ instance (\d+)"
)
PORT_RE = re.compile(r"instanceID=(\d+).*?api_server_port:(\d+)", re.S)
# Fallback: "Instance:neutral-<ip>:<port>-" appears in scheduling_selectors lines
INSTVIEW_RE = re.compile(r"instance (\d+) instanceView:.*?:(\d{4})-", re.S)
MIGRATE_RE = re.compile(r"([0-9a-fA-F-]{8,})")


def normalise(rid: str) -> str:
    """'cmpl-<uuid>' / 'chatcmpl-<uuid>' / '<uuid>-0' -> '<uuid>'."""
    if not rid:
        return ""
    s = rid.strip()
    for p in ("chatcmpl-", "cmpl-"):
        if s.startswith(p):
            s = s[len(p):]
            break
    return re.sub(r"-\d+$", "", s)


def parse_dispatch_log(path):
    """Return (uuid->instance_id, instance_id->port, migrated_uuids)."""
    uuid2inst, inst2port, migrated = {}, {}, set()
    if not os.path.isfile(path):
        return uuid2inst, inst2port, migrated
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = DISPATCH_RE.search(line)
            if m:
                # first dispatch wins (initial placement)
                uuid2inst.setdefault(normalise(m.group(1)), m.group(2))
                continue
            m = PORT_RE.search(line)
            if m:
                inst2port[m.group(1)] = int(m.group(2))
                continue
            m = INSTVIEW_RE.search(line)
            if m:
                inst2port.setdefault(m.group(1), int(m.group(2)))
                continue
            if "Migration" in line or "rescheduling pairs" in line:
                for u in MIGRATE_RE.findall(line):
                    if "-" in u:
                        migrated.add(normalise(u))
    return uuid2inst, inst2port, migrated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--out", default=None,
                    help="default <run_dir>/analysis/request_engine.csv")
    a = ap.parse_args()

    ids_path = os.path.join(a.run_dir, "request_ids.jsonl")
    log_path = os.path.join(a.run_dir, "server_metrics", "scheduler_dispatch.log")
    if not os.path.isfile(ids_path):
        sys.exit(f"missing {ids_path} (run predates the request-id sidecar?)")

    uuid2inst, inst2port, migrated = parse_dispatch_log(log_path)

    out_path = a.out or os.path.join(a.run_dir, "analysis", "request_engine.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    n = matched = 0
    per_port = {}
    with open(ids_path, encoding="utf-8") as f, \
            open(out_path, "w", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        w.writerow(["task_id", "call_index", "request_id", "uuid",
                    "instance_id", "engine_port", "migrated"])
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            n += 1
            u = normalise(r.get("request_id", ""))
            inst = uuid2inst.get(u, "")
            port = inst2port.get(inst, "") if inst else ""
            if inst:
                matched += 1
                if port:
                    per_port[port] = per_port.get(port, 0) + 1
            w.writerow([r.get("task_id", ""), r.get("call_index", ""),
                        r.get("request_id", ""), u, inst, port,
                        u in migrated])

    rate = 100.0 * matched / n if n else 0.0
    print(f"requests with a captured id : {n}")
    print(f"matched to a dispatch line  : {matched} ({rate:.1f}%)")
    print(f"instance->port map entries  : {len(inst2port)} {sorted(inst2port.values())}")
    print(f"per-engine counts           : {dict(sorted(per_port.items()))}")
    print(f"migration-flagged uuids     : {len(migrated)}")
    print(f"wrote: {out_path}")
    if rate < 90 and n:
        print("WARNING: low match rate — dispatch log may have been truncated "
              "or started late (collector starts after the cold restart).")


if __name__ == "__main__":
    main()
