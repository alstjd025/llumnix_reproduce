#!/usr/bin/env python3
"""The crossover footprint T*: which of an instance's two ceilings binds first.

An instance has two ceilings on how many requests it can hold at once.

  the PACE ceiling      the decode step time reaches the per-token budget the
                        instance has promised. From the fitted decode step law
                        `t = c0 + c_kv*M + c_n*n` (M is the LOGICAL KV count,
                        the sum of the resident requests' context lengths, which
                        is what the fit was taken against) plus the prefill that
                        steady state forces into every step: generating n tokens
                        per step costs n*rho uncached prompt tokens of prefill
                        at rate R, so

                            B = c0 + c_kv*n*T + c_n*n + n*rho/R
                            n_pace = (B - c0) / (c_kv*T + c_n + rho/R)

  the MEMORY ceiling    the physical KV pool fills. A resident request of
                        logical size T occupies s*T of the physical pool, s
                        being the measured physical-to-logical sharing ratio
                        that prefix caching produces, so

                            n_mem = C / (s*T)

Both are functions of the per-request resident footprint T. Setting them equal
gives the footprint at which the two ceilings meet:

    T* = C * (c_n + rho/R) / ((B - c0) * s - C * c_kv)

Below T* the pace ceiling is the lower one; above it the memory ceiling is. T*
is therefore a property of (model, tensor parallelism, KV pool) and of the class
budget alone -- no workload term appears in it -- while which side of it the
system sits on is a property of the workload. That separation is the point: it
predicts, from the profile tables and the pool size, whether a per-class
per-token budget model can bind on a given fleet at all.

WHAT IS MEASURED AND WHAT IS ASSUMED
  C     the instance's physical KV pool, in tokens. See `POOLS` -- this is the
        one input that is not recoverable from a run and the one the answer is
        most sensitive to.
  c0, c_kv, c_n, R   read from `deploy/profiling/<dir>/fluidserve.json`. The
        decode law is fitted on decode-only steps; R is the prefill rate taken
        at the 8192-token anchor, the largest measured chunk.
  s, rho, and the resident footprint   measured per fleet from the run, over the
        load window only (first arrival + 60 s to last arrival - 20 s, the same
        window `exp22_fluidserve.load_run` scores over). Cutting matters: the
        collector scrapes from before the load starts until after it drains, so
        a whole-run quantile of an engine gauge is half idle.

  python3 crossover_footprint.py            # prints the table
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(os.path.dirname(HERE))                  # experiment repo
REPO = os.path.dirname(os.path.dirname(EXP))                  # llumnix_reproduce
PROFILES = os.path.join(REPO, "deploy", "profiling")

WARMUP_S, DRAIN_S = 60.0, 20.0        # identical to exp22_fluidserve.load_run
CLASS_BUDGET_MS = {"chat": 50.0, "swe": 75.0, "deepresearch": 100.0}

# --- the fleets -------------------------------------------------------------
#
# `pool_tokens` is the instance's PHYSICAL KV pool. `pool_source` says how it is
# known, because the three are not known the same way and the difference has to
# travel with the number:
#
#   8B     engine-reported, recorded in ms_dev/notes/STATUS.md section 1 item 4.
#          NOT re-derivable from a run: no run stores the engine's block count,
#          and the runtime gauge `vllm:kv_cache_usage_perc` is a fraction whose
#          denominator is a different, smaller number (see `pool_audit`).
#   70B    the value every note in this repository has carried since 2026-08.
#          `pool_audit` shows the 8B value predicts it to +0.7% through the
#          per-GPU memory budget, which is the check that makes the 8B value
#          usable.
#   Qwen   NOT measured. Derived from the same memory budget with the overhead
#          the 8B value implies. Any statement about the Qwen fleet's T* rests
#          on that derivation and must say so.
FLEETS = {
    "4 x Llama-3.1-70B (TP=2)": dict(
        profile="llama31-70b-b200-tp2", pool_tokens=584928, pool_source="measured",
        gpus_per_instance=2, params=70_553_706_496, kv_bytes_per_token=327_680,
        run="results/260901_1514_exp109r2_fsv3capgnofrct75_shift", short="70B x4"),
    "4 x Qwen2.5-72B (TP=2)": dict(
        profile="qwen25-72b-b200-tp2", pool_tokens=None, pool_source="derived",
        gpus_per_instance=2, params=72_706_203_648, kv_bytes_per_token=327_680,
        run="results/260903_1031_exp113r2_fsv3capgnofrct75_shiftq", short="Qwen72B x4"),
    "8 x Llama-3.1-8B (TP=1)": dict(
        profile="llama31-8b-b200-tp1", pool_tokens=1152240, pool_source="engine",
        gpus_per_instance=1, params=8_030_261_248, kv_bytes_per_token=131_072,
        run="results/260908_2055_exp114h62r1_fsv3capgnofrct75_shift62", short="8B x8"),
}
# The per-GPU memory budget the engines are given. 183,359 MiB is what
# `nvidia-smi --query-gpu=memory.total` reports for these B200s and 0.9 is
# `--gpu-memory-utilization` in deploy/neutral/full-mode-scheduling/.../neutral.yaml.
GPU_BYTES = 183359 * 1024 * 1024
GPU_UTIL = 0.9
DTYPE_BYTES = 2                       # bfloat16 weights and KV cache


# --- profile ---------------------------------------------------------------
def profile(name):
    o = json.load(open(os.path.join(PROFILES, name, "fluidserve.json")))
    d, p = o["decode_step_law"], o["prefill_step_law"]
    return dict(c0=d["c0_ms"], c_kv=d["c_kv_ms_per_token"],
                c_n=d["c_n_ms_per_request"],
                # tokens per millisecond at the largest measured prefill chunk.
                # The three anchors do not lie on a line through the origin
                # (there is a fixed per-call cost), and 8192 is both the chunk
                # the engines are configured with and the one closest to a
                # saturated prefill.
                R=8192.0 / p["ms_at_8192"], prefill_ms_8192=p["ms_at_8192"])


def tstar(C, prof, B, s, rho):
    """The crossover footprint, in logical KV tokens per resident request.

    Returns +inf where the denominator is non-positive: there the pace ceiling
    lies below the memory ceiling at every footprint, so the crossover does not
    exist and memory can never be the binding constraint. That happens below
    `B = c0 + C*c_kv/s`, which is the budget at which a full pool alone already
    costs the whole step time.
    """
    den = (B - prof["c0"]) * s - C * prof["c_kv"]
    num = C * (prof["c_n"] + rho / prof["R"])
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(np.asarray(den) > 0, num / np.asarray(den), np.inf)
    return out if np.ndim(out) else float(out)


# --- run data ---------------------------------------------------------------
def load_window(run):
    p = os.path.join(run, "metrics.csv")
    m = pd.read_csv(p, usecols=["agent", "start_time"], low_memory=False)
    st = pd.to_numeric(m[m.agent != "job_summary"]["start_time"],
                       errors="coerce").dropna()
    return float(st.min()) + WARMUP_S, float(st.max()) - DRAIN_S


def sched_gauge(run, prefix):
    """[(t, {instance_id: value})] for one per-instance scheduler gauge."""
    out = []
    for line in open(os.path.join(run, "server_metrics", "scheduler.jsonl")):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("t") is None:
            continue
        d = {k.split("=")[1]: v for k, v in o.items() if k.startswith(prefix + "|")}
        if d:
            out.append((float(o["t"]), d))
    return out


def sched_counter(run, name, label):
    """The last scrape's value of one counter family, summed by one label.

    ⚠ THE LABEL SET IS NOT THE SAME ACROSS BINARIES. The eight-instance repeats
    were collected under two schedulers and the later one emits this counter
    with a second label, so the series key is `reason=gate,tier=25` rather than
    `reason=gate`. Splitting the key on the first `=` and taking what follows --
    which is what this function used to do -- returns `gate,tier` on one run and
    `gate` on the other, and the two never match, so one run's pace share reads
    0.0% and nothing says why. The label is parsed by NAME and the values are
    summed over every other label.
    """
    last = {}
    for line in open(os.path.join(run, "server_metrics", "scheduler.jsonl")):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        for k, v in o.items():
            if not k.startswith(name + "|"):
                continue
            pairs = dict(p.split("=", 1) for p in k.split("|", 1)[1].split(",")
                         if "=" in p)
            if label in pairs:
                last[k] = (pairs[label], v)
    out = {}
    for _, (lab, v) in last.items():
        out[lab] = out.get(lab, 0.0) + v
    return out


def engine_gauge(run, name):
    E = {}
    for f in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        port = os.path.basename(f).split("_")[1].split(".")[0]
        t, v = [], []
        for line in open(f):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            x = next((o[k] for k in o if k.startswith(name)), None)
            if x is None or o.get("t") is None:
                continue
            t.append(float(o["t"]))
            v.append(float(x))
        if len(t) > 3:
            E[port] = (np.array(t), np.array(v))
    return E


def counter_delta(run, name, lo, hi):
    """Fleet total increment of an engine counter inside the load window.

    Taken as an increment between the first and last scrape INSIDE the window
    rather than as a whole-run total, for the same reason the quantiles are
    windowed: the collector runs before the load starts and after it drains.
    """
    tot = 0.0
    for _, (t, v) in engine_gauge(run, name).items():
        m = (t >= lo) & (t <= hi)
        if m.sum() >= 2:
            tot += float(v[m][-1] - v[m][0])
    return tot


def measure(run):
    """s, rho, the resident footprint, and the pace-binding share, windowed.

    Every ratio here is formed PAIRED -- per scrape, or as a ratio of two sums
    over the same interval -- and never as one median divided by another. A
    ratio of two medians is a third quantity with no name, and this repository
    has had a conclusion change sign from exactly that mistake.
    """
    lo, hi = load_window(run)
    kv = sched_gauge(run, "scheduler_fluidserve_obs_kv_tokens")
    nb = dict(sched_gauge(run, "scheduler_fluidserve_obs_decode_batch"))
    perc = engine_gauge(run, "vllm:kv_cache_usage_perc")

    # Resident footprint: logical KV tokens per resident decoding request, one
    # observation per instance per scrape. Instances with a nearly empty batch
    # are dropped -- a ratio over 10 requests is not a footprint, it is noise --
    # and the threshold is reported.
    foot, occ_t, occ_log, drop = [], [], [], 0
    for t, d in kv:
        if not (lo <= t <= hi):
            continue
        b = nb.get(t)
        if b is None:
            continue
        tot = 0.0
        for i, v in d.items():
            n = b.get(i, 0.0)
            tot += v
            if v > 1000 and n >= 10:
                foot.append(v / n)
            else:
                drop += 1
        occ_t.append(t)
        occ_log.append(tot)
    foot = np.asarray(foot, dtype=float)

    # s = physical / logical, per scrape, over the whole fleet. The physical
    # side is the engines' own occupancy fraction; it is returned here as the
    # fleet's summed fraction divided by the summed logical count, so the caller
    # multiplies by the pool it has decided on and the choice of pool stays in
    # one place.
    occ_t, occ_log = np.asarray(occ_t), np.asarray(occ_log)
    psum = np.zeros_like(occ_t)
    for _, (te, ve) in perc.items():
        psum += np.interp(occ_t, te, ve, left=np.nan, right=np.nan)
    good = np.isfinite(psum) & (occ_log > 0)
    s_per_pool = psum[good] / occ_log[good]        # multiply by C to get s

    # rho: uncached prompt tokens prefilled per generated token. The engine's
    # `prompt_tokens_total` counts the WHOLE prompt -- measured against the
    # client's own submitted input tokens it agrees to within 2% -- so the
    # prefix-cache hit rate has to be taken out of it to leave the tokens that
    # were actually recomputed.
    P = counter_delta(run, "vllm:prompt_tokens_total", lo, hi)
    G = counter_delta(run, "vllm:generation_tokens_total", lo, hi)
    H = counter_delta(run, "vllm:prefix_cache_hits_total", lo, hi)
    Q = counter_delta(run, "vllm:prefix_cache_queries_total", lo, hi)
    hit = H / Q if Q > 0 else np.nan

    # The scheduler's own account of which ceiling refused a candidate. This is
    # a count of CANDIDATE EVALUATIONS, not of request decisions: one rejected
    # request contributes one evaluation per instance. `sole` is the subset in
    # which exactly one reason applied, which is the only subset in which the
    # counter names a binding ceiling rather than a coincidence.
    sole = sched_counter(run, "scheduler_fluidserve_infeasible_sole_total",
                         "reason")
    tot_sole = sum(sole.values())
    return dict(
        window_s=hi - lo, t_lo=lo, t_hi=hi,
        n_foot=len(foot), n_foot_dropped=drop,
        foot_p25=float(np.percentile(foot, 25)),
        foot_p50=float(np.median(foot)),
        foot_p75=float(np.percentile(foot, 75)),
        footprints=foot,
        s_per_pool=float(np.median(s_per_pool)), n_s=int(good.sum()),
        prompt_tok=P, gen_tok=G, prefix_hit_rate=hit,
        prompt_per_gen=P / G if G else np.nan,
        rho=(P / G) * (1.0 - hit) if G and Q else np.nan,
        sole_gate=sole.get("gate", 0.0), sole_memory=sole.get("memory", 0.0),
        sole_incumbents=sole.get("incumbents", 0.0),
        sole_other=tot_sole - sole.get("gate", 0.0) - sole.get("memory", 0.0),
        sole_total=tot_sole,
        counted_pace_frac=sole.get("gate", 0.0) / tot_sole if tot_sole else np.nan,
    )



# --- the one audit that needs no pool assumption ----------------------------
FS_MEMORY_SAFETY = 0.95               # pkg/scheduler/policy/fluidserve.go:88


def sharing_audit(run):
    """s straight out of the scheduler, and what pool `kv_cache_usage_perc` counts.

    `fluidserve.go:1310` computes `capMem = kvCapacity * 0.95 / ratio` where
    `ratio` is exactly the physical-to-logical sharing ratio this figure calls
    `s`, clamped into [0.01, 1]. Both `capMem` and the logical KV count are
    exported per instance, so on a run that carries the `cap_mem_tokens` series:

      kvCapacity = min(capMem) / 0.95      -- the clamp at ratio = 1 is the floor
      s_i        = min(capMem) / capMem_i  -- kvCapacity cancels

    That gives s with NO assumption about the pool at all. It also gives the
    physical token count per instance, `s_i * logical_i`, and dividing the
    fleet's total by the engines' summed `kv_cache_usage_perc` recovers the pool
    that gauge is a fraction OF -- which is not necessarily the pool the engine
    profiled at start-up.

    Returns None on a run predating the `cap_mem_tokens` series (the 70B and
    Qwen runs here), which is why the other estimator exists.
    """
    cm = sched_gauge(run, "scheduler_fluidserve_cap_mem_tokens")
    if not cm:
        return None
    lo, hi = load_window(run)
    kv = dict(sched_gauge(run, "scheduler_fluidserve_obs_kv_tokens"))
    perc = engine_gauge(run, "vllm:kv_cache_usage_perc")
    floor = min(v for _, d in cm for v in d.values() if v > 0)
    num, den, cs = 0.0, 0.0, []
    for t, d in cm:
        if not (lo <= t <= hi):
            continue
        k = kv.get(t)
        if not k:
            continue
        phys = tot = 0.0
        for i, v in d.items():
            if v <= 0 or i not in k or k[i] <= 1000:
                continue
            phys += (floor / v) * k[i]
            tot += k[i]
        if tot <= 0:
            continue
        num += phys
        den += tot
        p = sum(float(np.interp(t, te, ve, left=np.nan, right=np.nan))
                for _, (te, ve) in perc.items())
        if np.isfinite(p) and p > 0:
            cs.append(phys / p)
    return dict(kv_capacity=floor / FS_MEMORY_SAFETY,
                s_paired=num / den if den else np.nan,
                perc_denominator=float(np.median(cs)) if cs else np.nan,
                n=len(cs))


def pool_audit(fleets):
    """Is the pool value used for each fleet consistent with the memory budget?

    vLLM sizes the KV pool with what is left of the per-GPU budget after the
    weights and its own overhead, so one overhead constant has to explain every
    fleet on the same hardware. Calibrating that constant on ONE fleet's pool
    and predicting another's is therefore a real test of the first value, and it
    is the only test available offline.
    """
    avail = GPU_BYTES * GPU_UTIL
    rows = []
    for name, f in fleets.items():
        w = f["params"] * DTYPE_BYTES / f["gpus_per_instance"]
        row = dict(fleet=name, weights_gb_per_gpu=w / 1e9,
                   avail_gb_per_gpu=avail / 1e9)
        if f["pool_tokens"]:
            kvb = f["pool_tokens"] * f["kv_bytes_per_token"] / f["gpus_per_instance"]
            row["pool_tokens"] = f["pool_tokens"]
            row["kv_gb_per_gpu"] = kvb / 1e9
            row["implied_overhead_gb_per_gpu"] = (avail - w - kvb) / 1e9
        rows.append(row)
    return pd.DataFrame(rows)


def pool_from_overhead(f, overhead_bytes):
    avail = GPU_BYTES * GPU_UTIL
    w = f["params"] * DTYPE_BYTES / f["gpus_per_instance"]
    kvb = avail - w - overhead_bytes
    return kvb * f["gpus_per_instance"] / f["kv_bytes_per_token"]


def main():
    os.chdir(EXP)
    print("=== pool audit ===")
    print(pool_audit(FLEETS).to_string(index=False, float_format="%.3f"))
    f8 = FLEETS["8 x Llama-3.1-8B (TP=1)"]
    ov = (GPU_BYTES * GPU_UTIL - f8["params"] * DTYPE_BYTES
          - f8["pool_tokens"] * f8["kv_bytes_per_token"])
    f70 = FLEETS["4 x Llama-3.1-70B (TP=2)"]
    pred = pool_from_overhead(f70, ov)
    print(f"\noverhead from the 8B engine pool: {ov / 1e9:.3f} GB/GPU")
    print(f"  -> predicted 70B pool {pred:,.0f} vs measured {f70['pool_tokens']:,} "
          f"({100 * (pred / f70['pool_tokens'] - 1):+.2f}%)")
    sch = 1_037_648
    ov2 = GPU_BYTES * GPU_UTIL - f8["params"] * DTYPE_BYTES - sch * f8["kv_bytes_per_token"]
    pred2 = pool_from_overhead(f70, ov2)
    print(f"overhead from the scheduler's kvCapacity {sch:,}: {ov2 / 1e9:.3f} GB/GPU")
    print(f"  -> predicted 70B pool {pred2:,.0f} vs measured {f70['pool_tokens']:,} "
          f"({100 * (pred2 / f70['pool_tokens'] - 1):+.2f}%)")
    fq = FLEETS["4 x Qwen2.5-72B (TP=2)"]
    print(f"Qwen pool derived from the same overhead: "
          f"{pool_from_overhead(fq, ov):,.0f} tokens")

    print("\n=== sharing ratio straight from the scheduler, where available ===")
    for name, f in FLEETS.items():
        a = sharing_audit(f["run"])
        if a is None:
            print(f"{name}: no cap_mem_tokens series (run predates it)")
            continue
        print(f"{name}: scheduler kvCapacity {a['kv_capacity']:,.0f}, "
              f"s (paired) {a['s_paired']:.3f}, and `kv_cache_usage_perc` is a "
              f"fraction of {a['perc_denominator']:,.0f} tokens (n={a['n']})")

    print("\n=== per-fleet measurements (load window only) ===")
    for name, f in FLEETS.items():
        m = measure(f["run"])
        C = f["pool_tokens"] or pool_from_overhead(f, ov)
        s = m["s_per_pool"] * C
        p = profile(f["profile"])
        T = tstar(C, p, 50.0, s, m["rho"])
        pred_pace = float((m["footprints"] < T).mean())
        print(f"{name}")
        print(f"  window {m['window_s']:.0f}s  C={C:,.0f}  s={s:.3f}  "
              f"rho={m['rho']:.3f} (prompt/gen {m['prompt_per_gen']:.3f}, "
              f"prefix hit {m['prefix_hit_rate']:.3f})")
        print(f"  footprint p25/p50/p75 = {m['foot_p25']:.0f} / {m['foot_p50']:.0f}"
              f" / {m['foot_p75']:.0f}  (n={m['n_foot']:,}, {m['n_foot_dropped']:,} dropped)")
        print(f"  T*(chat 50ms) = {T:,.0f}   pace binds: predicted {100*pred_pace:.1f}%"
              f"  counted {100*m['counted_pace_frac']:.1f}%"
              f"  (sole gate {m['sole_gate']:,.0f} / total {m['sole_total']:,.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
