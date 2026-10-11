#!/usr/bin/env python3
"""Does the client read path group SSE events on its own?

Runs the load generator's exact reading path -- requests.Session.post(...,
stream=True) followed by resp.iter_lines(decode_unicode=True), the same calls
as LlumnixCompletionsLLM.stream() in workloads/swe_bench_coding/agent.py --
against a local SSE server whose emission pattern is known exactly.  Loopback
only: no cluster, no gateway, no engine.

Three server behaviours:
  even     one event every `step` ms, each flushed on its own.  If the client
           library grouped events by itself, this would show sub-tau gaps.
  grouped  `group` events written back to back every group*step ms.  This is
           what a proxy that stops and resumes would put on the wire.
  throttled  events at `step` ms, but the writer sleeps `stall` ms every
           `period` ms, imitating a CFS-throttled proxy.

Reports the same statistic the analysis uses: the share of inter-arrival gaps
below tau, and the quantiles of the gap distribution.

Usage:
  tail2026_client_loopback.py [--step 21] [--events 600] [--streams 8]
                              [--payload 268] [--tau 5]
"""
import argparse
import json
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

CFG = {}


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    pos = p * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        if n:
            self.rfile.read(n)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        mode = CFG["mode"]
        step = CFG["step"] / 1000.0
        pad = CFG["payload"]
        total = CFG["events"]

        def line(i):
            # same shape as the gateway's /v1/completions stream event
            body = {
                "id": "cmpl-%032x" % i,
                "object": "text_completion",
                "created": 1786201330,
                "model": "meta-llama/Meta-Llama-3.1-70B-Instruct",
                "choices": [{"text": "abcd", "index": 0,
                             "finish_reason": None, "logprobs": None}],
                "usage": None,
                "system_fingerprint": "",
            }
            s = "data: " + json.dumps(body, separators=(",", ":")) + "\n\n"
            if len(s) < pad:
                s = s[:-2] + " " * (pad - len(s)) + "\n\n"
            return s.encode()

        def emit(b):
            self.wfile.write(b"%x\r\n" % len(b) + b + b"\r\n")
            self.wfile.flush()

        try:
            if mode == "even":
                t = time.perf_counter()
                for i in range(total):
                    emit(line(i))
                    t += step
                    d = t - time.perf_counter()
                    if d > 0:
                        time.sleep(d)
            elif mode == "grouped":
                g = CFG["group"]
                t = time.perf_counter()
                for i in range(0, total, g):
                    for j in range(i, min(i + g, total)):
                        emit(line(j))
                    t += step * g
                    d = t - time.perf_counter()
                    if d > 0:
                        time.sleep(d)
            else:  # throttled
                stall = CFG["stall"] / 1000.0
                period = CFG["period"] / 1000.0
                t0 = time.perf_counter()
                t = t0
                nxt_stall = t0 + period
                for i in range(total):
                    now = time.perf_counter()
                    if now >= nxt_stall:
                        time.sleep(stall)
                        nxt_stall += period
                    emit(line(i))
                    t += step
                    d = t - time.perf_counter()
                    if d > 0:
                        time.sleep(d)
            emit(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def reader(url, out, lock, sess):
    gaps = []
    sizes = []
    last = None
    r = sess.post(url, json={"stream": True}, stream=True, timeout=120)
    for raw in r.iter_lines(decode_unicode=True):
        now = time.perf_counter()
        if not raw or not raw.startswith("data:"):
            continue
        data = raw[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
            obj["choices"][0].get("text", "")
        except (ValueError, KeyError, IndexError):
            continue
        sizes.append(len(raw) + 2)
        if last is not None:
            gaps.append((now - last) * 1000.0)
        last = now
    with lock:
        out.append((gaps, sizes))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=21.0, help="ms between tokens")
    ap.add_argument("--events", type=int, default=600)
    ap.add_argument("--streams", type=int, default=8)
    ap.add_argument("--payload", type=int, default=268, help="bytes per SSE event")
    ap.add_argument("--tau", type=float, default=5.0)
    ap.add_argument("--group", type=int, default=3)
    ap.add_argument("--stall", type=float, default=50.0)
    ap.add_argument("--period", type=float, default=310.0)
    ap.add_argument("--modes", default="even,grouped,throttled")
    a = ap.parse_args()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/v1/completions"

    sess = requests.Session()
    ad = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64,
                                       max_retries=0)
    sess.mount("http://", ad)

    print(f"loopback SSE, {a.streams} concurrent streams, {a.events} events each, "
          f"nominal step {a.step} ms, {a.payload} bytes/event")
    print(f"requests {requests.__version__}, urllib3 "
          f"{__import__('urllib3').__version__}, "
          f"iter_lines chunk_size={requests.models.ITER_CHUNK_SIZE}")
    print(f"{a.payload} byte events -> {512/a.payload:.2f} events per 512-byte "
          f"read block, {8192/a.payload:.1f} per 8 KiB socket buffer\n")

    hdr = (f"{'mode':10s} {'gaps':>8s} {'<tau %':>8s} {'p1':>8s} {'p10':>8s} "
           f"{'p50':>8s} {'p90':>8s} {'p99':>8s} {'mean':>8s} {'bytes':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for mode in a.modes.split(","):
        CFG.clear()
        CFG.update(mode=mode, step=a.step, events=a.events, payload=a.payload,
                   group=a.group, stall=a.stall, period=a.period)
        out, lock = [], threading.Lock()
        th = [threading.Thread(target=reader, args=(url, out, lock, sess))
              for _ in range(a.streams)]
        for t in th:
            t.start()
        for t in th:
            t.join()
        gaps = [g for gs, _ in out for g in gs]
        sizes = [s for _, ss in out for s in ss]
        if not gaps:
            print(f"{mode:10s} no data")
            continue
        frac = sum(1 for g in gaps if g < a.tau) / len(gaps)
        print(f"{mode:10s} {len(gaps):8d} {frac*100:8.2f} {q(gaps,.01):8.3f} "
              f"{q(gaps,.10):8.3f} {q(gaps,.50):8.3f} {q(gaps,.90):8.3f} "
              f"{q(gaps,.99):8.3f} {statistics.fmean(gaps):8.3f} "
              f"{statistics.median(sizes):6.0f}")
    srv.shutdown()


if __name__ == "__main__":
    main()
