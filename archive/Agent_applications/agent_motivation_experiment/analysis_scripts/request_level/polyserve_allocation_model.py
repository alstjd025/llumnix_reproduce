"""Reproduce PolyServe's allocateServers off-line, to see whether the partition
can move at all under a given workload.

Mirrors `serverSecondsPerRequest`, `maxBatchForTpot` and `allocateServers` in
llumnix/pkg/scheduler/policy/polyserve_repartition.go, reading the same latency
profiling tables the scheduler loads.

It exists because a comparison against a static partition is vacuous if the
partition never has to move, and whether it moves is decided by the workload
rather than by the policy. Measured on the cluster the repartitioner changed the
allocation 0 times in 459 samples of a 7-minute dynamic run; running this first
says why -- with an equal-request-count mix the demand estimate is dominated by
the agent class for every ratio down to 5% agent traffic, so the answer is
(2 swe / 1 chat / 1 dr) whatever the mix does.

Answering that question here costs seconds instead of the four hours a cluster
sweep costs, and it is what led to defining the EXP-27 mixes by input-token
share rather than by request count.

    python3 analysis_scripts/request_level/polyserve_allocation_model.py
"""
import json, os, sys

TD = os.environ.get('PROFILE_DIR',
      '/home/nxclab/llumnix_reproduce/deploy/profiling/llama31-70b-b200-tp2')
ttft = json.load(open(f'{TD}/ttft.json'))
tpot = json.load(open(f'{TD}/tpot.json'))

# --- prefill: per-step cost vs chunk size, linear interpolation on tokens_num
tt = sorted((r['tokens_num'], r['mean']) for r in ttft['results'])
def prefill_step_ms(n):
    if n <= tt[0][0]: return tt[0][1]
    if n >= tt[-1][0]: return tt[-1][1]
    for (x0,y0),(x1,y1) in zip(tt, tt[1:]):
        if x0 <= n <= x1:
            return y0 + (y1-y0)*(n-x0)/(x1-x0)
    return tt[-1][1]

# --- decode: bilinear over (batch_size, tokens_per_request)
cells = {(r['batch_size'], r['tokens_per_request']): r['mean'] for r in tpot['results']}
BS = sorted({b for b,_ in cells}); TK = sorted({t for _,t in cells})
def _bracket(vals, x):
    if x <= vals[0]: return vals[0], vals[0]
    if x >= vals[-1]: return None                      # out of grid -> +Inf in Go
    for a,b in zip(vals, vals[1:]):
        if a <= x <= b: return a,b
    return None
def tpot_ms(batch, total_kv):
    tok = total_kv/max(batch,1)
    bb = _bracket(BS, batch); tb = _bracket(TK, tok)
    if bb is None or tb is None: return float('inf')
    (b0,b1),(t0,t1) = bb, tb
    def at(b,t): return cells.get((b,t))
    q = [at(b0,t0), at(b1,t0), at(b0,t1), at(b1,t1)]
    if any(v is None for v in q): return float('inf')
    fb = 0 if b1==b0 else (batch-b0)/(b1-b0)
    ft = 0 if t1==t0 else (tok-t0)/(t1-t0)
    return (q[0]*(1-fb)*(1-ft) + q[1]*fb*(1-ft) + q[2]*(1-fb)*ft + q[3]*fb*ft)

MAX_PROBE_BATCH = 2048  # maxProbeBatch in polyserve_repartition.go
def max_batch_for_tpot(tpot_slo_ms, kv_per_req):
    best = 0; b = 1
    while b <= MAX_PROBE_BATCH:
        it = tpot_ms(b, b*kv_per_req)
        if it == float('inf') or it > tpot_slo_ms: break
        best = b; b *= 2
    return best or 1

MNBT = 8192
def server_seconds(tier_tpot_ms, mean_in, out_toks):
    tput = MNBT/(prefill_step_ms(MNBT)/1000.0)
    pre = mean_in/tput
    kv = mean_in + out_toks/2
    batch = max_batch_for_tpot(tier_tpot_ms, kv)
    dec = out_toks*(tier_tpot_ms/1000.0)/batch
    return pre+dec, batch

def allocate(demand, n):
    """Largest-remainder over n servers, then a floor of one per tier taken from
    the largest holder. Mirrors allocateServers in polyserve_repartition.go."""
    import math
    tiers = sorted(demand)
    tot = sum(d for d in demand.values() if d > 0)
    if n <= 0 or not tiers: return {}
    if n < len(tiers):
        order = sorted(tiers, key=lambda t: (-demand[t], t))[:n]
        return {t: 1 for t in order}
    if tot <= 0:
        base, extra = divmod(n, len(tiers))
        return {t: base + (1 if i < extra else 0) for i, t in enumerate(tiers)}
    exact = {t: n*max(demand[t], 0.0)/tot for t in tiers}
    out = {t: int(math.floor(exact[t])) for t in tiers}
    assigned = sum(out.values())
    rems = sorted(tiers, key=lambda t: (-(exact[t]-math.floor(exact[t])), t))
    i = 0
    while assigned < n:
        out[rems[i % len(rems)]] += 1
        assigned += 1; i += 1
    for t in tiers:                                   # min-1 floor
        if out[t] > 0: continue
        donor, cnt = None, 1
        for c in tiers:
            if out[c] > cnt: donor, cnt = c, out[c]
        if donor is None: continue
        out[donor] -= 1; out[t] += 1
    return out

TIERS = {'swe':25, 'chat':50, 'dr':100}
OUT   = {'swe':728, 'chat':386, 'dr':275}

def report(name, mean_in, mixes, nservers=4):
    print(f'\n===== {name}  (mean input: ' +
          ', '.join(f'{k} {v:,}' for k,v in mean_in.items()) + f')  servers={nservers}')
    cost = {}
    for c,t in TIERS.items():
        s,b = server_seconds(t, mean_in[c], OUT[c])
        cost[c] = s
        print(f'  {c:5s} tier {t:3d}ms  server-seconds/req {s:7.3f}  max batch {b}')
    print(f'  {"mix (requests)":34s} {"token share":26s} allocation')
    for label, w in mixes:
        tot_r = sum(w.values())
        rshare = {c: w[c]/tot_r for c in w}
        toks = {c: rshare[c]*mean_in[c] for c in w}
        tt_ = sum(toks.values())
        tshare = {c: toks[c]/tt_ for c in w}
        demand = {TIERS[c]: rshare[c]*cost[c] for c in w}
        alloc = allocate(demand, nservers)
        a = {c: alloc.get(TIERS[c],0) for c in ('swe','chat','dr')}
        print(f'  {label:34s} '
              f'{"/".join(f"{tshare[c]*100:.0f}%" for c in ("chat","dr","swe")):26s} '
              f'swe {a["swe"]} chat {a["chat"]} dr {a["dr"]}')

if __name__ == '__main__':
    MIXES_COUNT = [
        ('1:1:1 by request count', {'chat':1,'dr':1,'swe':1}),
    ]
    LONG = {'chat':674,'dr':4055,'swe':22474}
    SHORT= {'chat':674,'dr':4055,'swe':6812}
    def bylen(name, share, mi):
        # request counts that realise a target token share
        w = {c: share[c]/mi[c] for c in share}
        m = min(w.values()); w = {c: max(1, round(v/m)) for c,v in w.items()}
        return (f'{name} {w}', w)
    for mi, tag in ((LONG,'swe 22.5k (current)'), (SHORT,'swe 6.8k (short)')):
        mixes = MIXES_COUNT + [
            bylen('balanced 1:1:1 by tokens', {'chat':1,'dr':1,'swe':1}, mi),
            bylen('chat-heavy 4:1:1',         {'chat':4,'dr':1,'swe':1}, mi),
            bylen('swe-heavy 1:1:4',          {'chat':1,'dr':1,'swe':4}, mi),
        ]
        report(tag, mi, mixes)
