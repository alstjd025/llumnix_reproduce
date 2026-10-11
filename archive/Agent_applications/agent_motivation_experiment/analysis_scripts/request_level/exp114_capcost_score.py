"""Score the free-gate pair, and check the run set before believing any of it."""
import sys, glob, os, json, re, datetime
sys.path.insert(0, 'analysis_scripts/request_level')
import pandas as pd, numpy as np
from exp22_fluidserve import load_run, class_of, attain

ARM = {'fsv3capgnofrct75': 'cap ON', 'fsv3capgnofrct75cc': 'ON+free'}
TIER = {'chat': 50, 'swe': 25, 'deepresearch': 100}

runs = []
for d in sorted(glob.glob('results/*exp114ccr*_rpm_*')):
    n = os.path.basename(d)
    arm = next((v for k, v in ARM.items() if f'_{k}_' in n), None)
    if arm is None:
        print(f"  !! unrecognised arm in {n} -- not scored"); continue
    if os.path.isdir(os.path.join(d, 'shards')):
        print(f"  skipping {n}: shards/ left, so it died before merging")
        continue
    runs.append((d, n, arm, int(re.search(r'exp114ccr(\d)', n).group(1)),
                 int(re.search(r'rpm_(\d+)', n).group(1)) // 60))

print("=== validity checks, before any number is read ===")
print(f"  merged run directories: {len(runs)} (want 12)")
bad = [n for d, n, *_ in runs if os.path.isdir(os.path.join(d, 'shards'))]
print(f"  runs that died before merging (shards/ left): {len(bad)} {bad}")

rows = []
for d, n, arm, rep, rate in runs:
    m = pd.read_csv(os.path.join(d, 'metrics.csv'), low_memory=False)
    m = m[m['agent'] != 'job_summary']
    rows.append((n, arm, rep, rate, m['start_time'].min(), m['start_time'].max(), len(m)))
rows.sort(key=lambda r: r[4])
prev = None
overlap = 0
for n, arm, rep, rate, a, b, k in rows:
    if prev is not None and a < prev:
        print(f"  !! OVERLAPS the previous run: {n}"); overlap += 1
    prev = b
print(f"  runs whose load window overlaps another: {overlap} (any is disqualifying)")
print(f"  achieved arrival rate as a fraction of requested:")
for n, arm, rep, rate, a, b, k in rows:
    print(f"    {arm:<8s} rep{rep} {rate:4d} req/s -> {k/(b-a):6.1f} ({k/(b-a)/rate:.2f})")

print()
print("=== scores, mean [min-max] over repeats, per-request aggregation ===")
sc = []
for d, n, arm, rep, rate in runs:
    df = load_run(d); cl = df['task_id'].map(class_of); rej = df['is_rejected'].astype(bool)
    dur = df['start_time'].max() - df['start_time'].min()
    r = dict(arm=arm, rep=rep, rate=rate, off=attain(df, 'violate_offered'),
             adm=attain(df, 'violate_served'), rej=100 * rej.mean(),
             gp=df.loc[~rej, 'output_tokens'].sum() / dur)
    for c in TIER:
        s = df[cl == c]
        r[c + '_off'] = attain(s, 'violate_offered')
        r[c + '_rej'] = 100 * s['is_rejected'].astype(bool).mean()
    sc.append(r)
t = pd.DataFrame(sc)
def band(g, col): return f"{g[col].mean():5.1f} [{g[col].min():.1f}-{g[col].max():.1f}]"
print(f"{'req/s':>6s} {'arm':<8s} {'offered':>18s} {'admitted':>18s} {'rejected %':>18s} {'goodput':>9s}")
for (rate, arm), g in t.groupby(['rate', 'arm']):
    print(f"{rate:6d} {arm:<8s} {band(g,'off'):>18s} {band(g,'adm'):>18s} {band(g,'rej'):>18s} {g['gp'].mean():9,.0f}")
print()
print(f"{'req/s':>6s} {'arm':<8s} " + " ".join(f"{c[:4]+' offered':>18s}" for c in TIER))
for (rate, arm), g in t.groupby(['rate', 'arm']):
    print(f"{rate:6d} {arm:<8s} " + " ".join(f"{band(g,c+'_off'):>18s}" for c in TIER))

print()
print("=== did the new branch fire, and does it fire MORE as load rises? ===")
print("   (the concern: an empty instance has an infinite capKv, which the branch refuses")
print("    to extrapolate from, so at low load -- where spreading is cheapest -- it may fire least)")
print(f"{'req/s':>6s} {'rep':>4s} {'free-gate keeps':>16s} {'removals':>10s} {'kept %':>8s} "
      f"{'instances with capKv=-1 (p50 of 8)':>36s}")
for d, n, arm, rep, rate in sorted(runs, key=lambda r: (r[4], r[3])):
    if arm != 'ON+free': continue
    sj = os.path.join(d, 'server_metrics', 'scheduler.jsonl')
    rws = [json.loads(l) for l in open(sj)]
    last = rws[-1]
    free = sum(v for k, v in last.items() if 'instcap_free_gate_total|tier=50' in k)
    exc = sum(v for k, v in last.items() if 'instcap_excluded_total|tier=50' in k)
    ids = sorted({k.split('instance=')[1] for k in last if k.startswith('scheduler_fluidserve_cap_kv_tokens|')})
    rt = np.array([r.get('scheduler_fluidserve_decisions_total|decision=route', np.nan) for r in rws], float)
    loaded = np.r_[False, np.diff(np.nan_to_num(rt)) > 0]
    empty = []
    for j in range(len(rws)):
        if not loaded[j]: continue
        empty.append(sum(1 for i in ids
                         if rws[j].get(f'scheduler_fluidserve_cap_kv_tokens|instance={i}', 0) == -1))
    print(f"{rate:6d} {rep:4d} {free:16,.0f} {exc:10,.0f} {100*free/max(free+exc,1):7.1f}% "
          f"{np.median(empty) if empty else float('nan'):36.1f}")
