#!/usr/bin/env python3
"""Per-condition time series split into TWO images per rate condition:

  llumnix_rpm_<rpm>.png  — Llumnix control-plane layer only:
      A. gateway_current / gateway_pending / being_inferred (=current-pending)
      B. migration count (scheduler_rescheduling_total) + reject/error rate
         (gateway request_total by status). [auto-plots if captured; else a note]

  engine_rpm_<rpm>.png   — engine layer only, per engine (8000-8003):
      A. running (vllm:num_requests_running — execution batch, no queue)
      B. waiting (vllm:num_requests_waiting — engine-side queue)
      C. KV cache usage %
      D. token throughput: prefill (prompt tok/s) + decode (generation tok/s)
      E. KV cache hit rate (prefix_cache_hits/queries).  [if captured]
      F. queueing time (avg request_queue_time). [if captured]

The script auto-detects which series are present, so the same script produces the
partial plots from current runs and the full plots after the collector is
enhanced + the sweep re-run. Missing panels show a "not captured" note.
"""

import argparse
import glob
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# The fleet's size is a property of the run, not of this file. A literal
# 8000..8003 here drops half of an eight-instance fleet from every panel with
# nothing saying so, so the ports are read off the run's own server_metrics
# directory; $ENGINE_PORTS, then the historical four, are the fallbacks for a
# caller that has no run directory in hand.
def engine_ports_of(run=None):
    if run:
        found = sorted(
            int(os.path.basename(f)[len("engine_"):-len(".jsonl")])
            for f in glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl")))
        if found:
            return tuple(found)
    raw = os.environ.get("ENGINE_PORTS", "").strip()
    if raw:
        return tuple(int(p) for p in raw.split(",") if p.strip())
    return (8000, 8001, 8002, 8003)


ENGINE_PORTS = engine_ports_of()
SMOOTH = 3
PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 7, "legend.frameon": False,
         "lines.linewidth": 1.3}


def _load(path):
    if not os.path.isfile(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def gauge_recs(recs, key, t0=None):
    """(t_rel, summed value) for a gauge key across pre-loaded records."""
    ts, vs = [], []
    base = t0
    for r in recs:
        if not r.get("ok"):
            continue
        vals = [v for k, v in r.items()
                if k.split("|")[0] == key and isinstance(v, (int, float))]
        if not vals:
            continue
        if base is None:
            base = r["t"]
        ts.append(r["t"] - base)
        vs.append(sum(vals))
    return np.array(ts), np.array(vs), base


def rate_recs(recs, key, t0=None, label_filter=None):
    """per-second rate from a cumulative counter (optionally filtered by label substr)."""
    ts, vs, base = [], [], t0
    for r in recs:
        if not r.get("ok"):
            continue
        vals = [v for k, v in r.items()
                if k.split("|")[0] == key and isinstance(v, (int, float))
                and (label_filter is None or label_filter in k)]
        if not vals:
            continue
        if base is None:
            base = r["t"]
        ts.append(r["t"] - base)
        vs.append(sum(vals))
    t, v = np.array(ts), np.array(vs)
    if len(t) < 2:
        return np.array([]), np.array([]), base
    dt = np.diff(t)
    rr = np.clip(np.diff(v) / np.where(dt > 0, dt, 1), 0, None)
    tm = t[:-1] + dt / 2
    if SMOOTH > 1 and len(rr) >= SMOOTH:
        rr = np.convolve(rr, np.ones(SMOOTH) / SMOOTH, mode="same")
    return tm, rr, base


def _note(ax, msg):
    ax.text(0.5, 0.5, msg, ha="center", va="center", transform=ax.transAxes,
            fontsize=8, color="0.5", style="italic")



def _rate_phrase(reqps, tag):
    """How the condition is named in a suptitle.

    A dynamic trace has no single arrival rate, so the rate parsed out of the
    directory name falls back to 0 and the title then read "offered 0 req/s",
    which is not a missing value but a wrong one. When there is no rate, the
    run tag is printed instead.
    """
    try:
        r = float(reqps)
    except (TypeError, ValueError):
        r = 0.0
    if r > 0:
        return f"offered {r:g} req/s"
    return f"dynamic trace, no single rate ({tag})"

def plot_llumnix(run, rpm, out_dir, tag=None, rate_div=60.0):
    reqps = rpm / rate_div
    tag = tag or f"rpm_{rpm:g}"
    gw = _load(os.path.join(run, "server_metrics", "gateway.jsonl"))
    sc = _load(os.path.join(run, "server_metrics", "scheduler.jsonl"))
    with plt.rc_context(PAPER):
        fig, ax = plt.subplots(1, 2, figsize=(13, 4.5))
        # A. current / pending / being_inferred
        a = ax[0]
        ct, cv, t0 = gauge_recs(gw, "gateway_current_requests")
        pt, pv, _ = gauge_recs(gw, "gateway_pending_requests", t0)
        a.plot(ct, cv, color="#d62728", lw=1.8, label="gateway_current")
        a.plot(pt, pv, color="#ff7f0e", lw=1.4, ls="--", label="gateway_pending")
        n = min(len(cv), len(pv))
        if n:
            a.plot(ct[:n], np.clip(cv[:n] - pv[:n], 0, None), color="#1f77b4",
                   lw=1.4, ls=":", label="being_inferred (=current-pending)")
        a.set_title("A. Requests in gateway"); a.set_ylabel("requests")
        a.set_xlabel("time (s)"); a.legend(loc="upper right")

        # B. migration + reject/error/drop (gateway request_total by status_code)
        b = ax[1]
        plotted = False
        # completed(200) for context + failure classes: 499 client-closed(drop),
        # 503 reject, 400 bad-request.
        for status, col, lab in (("200", "#2ca02c", "200 completed/s"),
                                  ("499", "#d62728", "499 client-closed/s"),
                                  ("503", "#ff7f0e", "503 reject/s"),
                                  ("400", "#7f7f7f", "400 error/s")):
            rt, rv, _ = rate_recs(gw, "request_total", t0, label_filter=f"status_code={status}")
            if len(rv) and rv.max() > 0:
                b.plot(rt, rv, color=col, lw=1.3, label=lab); plotted = True
        # migration decisions (cumulative) on a twin axis, if captured
        mt, mv, _ = gauge_recs(sc, "scheduler_rescheduling_total")
        if len(mv) and mv.max() > 0:
            bt = b.twinx()
            bt.plot(mt, mv, color="#9467bd", lw=1.6, ls="--", label="migration (cum)")
            bt.set_ylabel("migration count", color="#9467bd")
            bt.legend(loc="upper left"); plotted = True
        b.set_title("B. Completed / reject / error / drop  (+migration)")
        b.set_xlabel("time (s)")
        if plotted:
            b.set_ylabel("requests/s"); b.legend(loc="upper right")
        else:
            _note(b, "request_total/rescheduling not captured\n(needs collector re-run)")
        fig.suptitle(f"Llumnix layer — {_rate_phrase(reqps, tag)}", y=1.0)
        fig.tight_layout()
        out = os.path.join(out_dir, f"llumnix_{tag}.png")
        fig.savefig(out, dpi=140, bbox_inches="tight"); plt.close(fig)
    return out


def plot_engine(run, rpm, out_dir, tag=None, rate_div=60.0):
    reqps = rpm / rate_div
    tag = tag or f"rpm_{rpm:g}"
    ports = engine_ports_of(run)
    recs = {p: _load(os.path.join(run, "server_metrics", f"engine_{p}.jsonl")) for p in ports}
    colors = plt.cm.tab10(np.arange(len(ports)) % 10)
    with plt.rc_context(PAPER):
        fig, ax = plt.subplots(2, 3, figsize=(16, 8))

        def per_engine_gauge(axis, key, ylabel, title, scale=1.0):
            for c, p in zip(colors, ports):
                t, v, _ = gauge_recs(recs[p], key)
                if len(t):
                    axis.plot(t, v * scale, color=c, lw=1.0, label=f"eng {p}")
            axis.set_title(title); axis.set_ylabel(ylabel); axis.set_xlabel("time (s)")
            axis.legend(ncol=2, fontsize=6)

        per_engine_gauge(ax[0, 0], "vllm:num_requests_running", "requests",
                         "A. RUNNING (execution batch; no queue)")
        per_engine_gauge(ax[0, 1], "vllm:num_requests_waiting", "requests",
                         "B. WAITING (engine-side queue)")
        per_engine_gauge(ax[0, 2], "vllm:kv_cache_usage_perc", "KV usage (%)",
                         "C. KV cache usage", scale=100.0)

        # D. token throughput prefill + decode (fleet Σ)
        d = ax[1, 0]
        for key, col, lab in (("vllm:prompt_tokens_total", "#9467bd", "prefill (prompt) tok/s"),
                              ("vllm:generation_tokens_total", "#2ca02c", "decode (generation) tok/s")):
            agg_t, agg = None, None
            for p in ports:
                t, r, _ = rate_recs(recs[p], key)
                if len(t):
                    if agg is None:
                        agg_t, agg = t, r.astype(float)
                    else:
                        m = min(len(agg), len(r)); agg_t, agg = agg_t[:m], agg[:m] + r[:m]
            if agg is not None:
                d.plot(agg_t, agg, color=col, lw=1.5, label=lab)
        d.set_title("D. Token throughput (fleet Σ)"); d.set_ylabel("tokens/s")
        d.set_xlabel("time (s)"); d.legend(loc="upper right")

        # E. KV cache hit rate (prefix_cache_hits/queries) if captured
        e = ax[1, 1]
        any_hit = False
        for c, p in zip(colors, ports):
            ht, hv, _ = rate_recs(recs[p], "vllm:prefix_cache_hits_total")
            qt, qv, _ = rate_recs(recs[p], "vllm:prefix_cache_queries_total")
            if len(hv) and len(qv):
                m = min(len(hv), len(qv))
                ratio = np.divide(hv[:m], np.where(qv[:m] > 0, qv[:m], np.nan)) * 100
                e.plot(ht[:m], ratio, color=c, lw=1.0, label=f"eng {p}")
                any_hit = True
        e.set_title("E. KV cache hit rate"); e.set_xlabel("time (s)")
        if any_hit:
            e.set_ylabel("prefix-cache hit (%)"); e.legend(ncol=2, fontsize=6)
        else:
            _note(e, "prefix_cache_hits/queries not captured\n(needs collector re-run)")

        # F. queueing time (avg = d(queue_time_sum)/d(count)) if captured
        f = ax[1, 2]
        any_q = False
        for c, p in zip(colors, ports):
            st, sv, t0 = gauge_recs(recs[p], "vllm:request_queue_time_seconds_sum")
            ntc, nvc, _ = gauge_recs(recs[p], "vllm:request_queue_time_seconds_count", t0)
            if len(sv) > 2 and len(nvc) > 2:
                m = min(len(sv), len(nvc))
                dsum = np.diff(sv[:m]); dcnt = np.diff(nvc[:m])
                avg = np.divide(dsum, np.where(dcnt > 0, dcnt, np.nan)) * 1000
                f.plot(st[:m - 1] if len(st) >= m else ntc[:m-1], avg, color=c, lw=1.0, label=f"eng {p}")
                any_q = True
        f.set_title("F. Queueing time (avg per request)"); f.set_xlabel("time (s)")
        if any_q:
            f.set_ylabel("queue time (ms)"); f.legend(ncol=2, fontsize=6)
        else:
            _note(f, "request_queue_time _count not captured\n(needs collector re-run)")

        for row in ax:
            for p in row:
                p.grid(axis="y", ls=":", lw=0.5, alpha=0.5)
        # The port range is read from the series that were drawn. It was
        # the literal "8000-8003" until 2026-09-11, by which time this
        # script was being run on eight-instance fleets and the title
        # named half the engines its own legend showed.
        _p = sorted(ports)
        _range = (f"per engine {_p[0]}-{_p[-1]}, {len(_p)} engines"
                  if _p else "no engine series")
        fig.suptitle(f"Engine layer — {_rate_phrase(reqps, tag)} "
                     f"({_range})", y=1.0)
        fig.tight_layout()
        out = os.path.join(out_dir, f"engine_{tag}.png")
        fig.savefig(out, dpi=140, bbox_inches="tight"); plt.close(fig)
    return out



def _windowed_mean(recs, sum_key, cnt_key, t0=None):
    """Interval mean of a histogram metric: d(sum)/d(count) per tick."""
    ts, ss, cs, base = [], [], [], t0
    for r in recs:
        if not r.get("ok"):
            continue
        sv = [v for k, v in r.items() if k.split("|")[0] == sum_key]
        cv = [v for k, v in r.items() if k.split("|")[0] == cnt_key]
        if not sv or not cv:
            continue
        if base is None:
            base = r["t"]
        ts.append(r["t"] - base); ss.append(sum(sv)); cs.append(sum(cv))
    t, sa, ca = np.array(ts), np.array(ss), np.array(cs)
    if len(t) < 2:
        return np.array([]), np.array([])
    ds, dc = np.diff(sa), np.diff(ca)
    m = dc > 0
    tm = (t[:-1] + np.diff(t) / 2)[m]
    mv = ds[m] / dc[m]
    if SMOOTH > 1 and len(mv) >= SMOOTH:
        mv = np.convolve(mv, np.ones(SMOOTH) / SMOOTH, mode="same")
    return tm, mv


def plot_tokens(run, rpm, out_dir, tag=None, rate_div=60.0):
    """Per-engine token throughput + latency, separate panels and scales.

    4x4 grid, one column per engine: prefill (prompt) tok/s, decode
    (generation) tok/s, mean TTFT (engine-side: from engine receipt incl.
    its waiting queue), mean ITL. NB vllm:prompt_tokens_total counts ALL
    scheduled prompt tokens including prefix-cache HITS (cheap attach),
    so the prefill row is volume, not pure compute.
    """
    reqps = rpm / rate_div
    tag = tag or f"rpm_{rpm:g}"
    ports = engine_ports_of(run)
    recs = {p: _load(os.path.join(run, "server_metrics", f"engine_{p}.jsonl"))
            for p in ports}
    with plt.rc_context(PAPER):
        fig, axes = plt.subplots(4, len(ports), figsize=(4.0 * len(ports), 11),
                                 sharex=True)
        for col, p in enumerate(ports):
            rows = (
                ("rate", "vllm:prompt_tokens_total", None,
                 "prefill tok/s\n(incl. cache hits)", "#9467bd", 1.0),
                ("rate", "vllm:generation_tokens_total", None,
                 "decode tok/s", "#2ca02c", 1.0),
                ("mean", "vllm:time_to_first_token_seconds_sum",
                 "vllm:time_to_first_token_seconds_count",
                 "mean TTFT (s)\n(engine-side)", "#1f77b4", 1.0),
                ("mean", "vllm:inter_token_latency_seconds_sum",
                 "vllm:inter_token_latency_seconds_count",
                 "mean ITL (ms)", "#d62728", 1000.0),
            )
            for row, (kind, k1, k2, lab, lc, scale) in enumerate(rows):
                axis = axes[row][col]
                if kind == "rate":
                    t, v, _ = rate_recs(recs[p], k1)
                else:
                    t, v = _windowed_mean(recs[p], k1, k2)
                if len(t):
                    axis.plot(t, v * scale, color=lc, lw=0.9)
                else:
                    _note(axis, "not captured")
                if row == 0:
                    axis.set_title(f"engine {p}", fontsize=9)
                if col == 0:
                    axis.set_ylabel(lab)
                axis.grid(axis="y", ls=":", lw=0.5, alpha=0.5)
                if row == len(rows) - 1:
                    axis.set_xlabel("time (s)")
        fig.suptitle(f"Per-engine token throughput & latency (separate scales) — "
                     f"{_rate_phrase(reqps, tag)}")
        fig.tight_layout()
        out = os.path.join(out_dir, f"tokens_{tag}.png")
        fig.savefig(out, dpi=140, bbox_inches="tight"); plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp02b_ratesweep_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp02b_plots")
    ap.add_argument("--rate-key", default="rpm_",
                    help="dirname token preceding the rate value (e.g. 'lambda_')")
    ap.add_argument("--rate-div", type=float, default=60.0,
                    help="divide the parsed value by this to get req/s")
    ap.add_argument("--rate", type=float, default=None,
                    help="offered rate to put in the titles when the directory "
                         "name carries no rate, as for a dynamic trace whose "
                         "rate varies. The tag then comes from the dirname.")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    # A dynamic-trace run has no rate in its name, so the sweep parse is tried
    # and the directory name is used as the tag when it does not apply. Without
    # this the script raised ValueError on any run that was not a rate sweep,
    # which is why the hour-long traces went without engine-layer figures.
    def parsed(x):
        try:
            return float(x.split(args.rate_key)[1])
        except (IndexError, ValueError):
            return None

    dirs = sorted(glob.glob(args.glob),
                  key=lambda x: (parsed(x) is None, parsed(x) or 0.0, x))
    written = []
    for run in dirs:
        p = parsed(run)
        rpm = p if p is not None else (args.rate or 0.0) * args.rate_div
        tag = f"{args.rate_key}{rpm:g}" if p is not None \
            else os.path.basename(run).split("_", 2)[-1]
        written.append(plot_llumnix(run, rpm, args.out_dir, tag, args.rate_div))
        written.append(plot_engine(run, rpm, args.out_dir, tag, args.rate_div))
        written.append(plot_tokens(run, rpm, args.out_dir, tag, args.rate_div))
    print("wrote:")
    for w in written:
        print(" ", w)


if __name__ == "__main__":
    main()
