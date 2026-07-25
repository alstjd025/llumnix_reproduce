# vllm-sched — custom intra-engine scheduling policies (EXP-15)

Custom vLLM V1 schedulers used to compare **FIFO / EDF / SJF / SRPF** inside the
serving engine, for the mixed-workload experiments (EXP-14 → EXP-15).

**No vLLM source file is modified.** We use vLLM's `--scheduler-cls` hook and
supply our own subclass. This means: no patch to maintain, no checksum drift, no
image rebuild — swapping policy is a deploy-flag change.

## Target

- vLLM `0.12.1.dev0+g4fd9d6a85.d20260305` (see `VLLM_VERSION.txt`), the version
  baked into the Llumnix engine image.
- Engine runs with `--async-scheduling`, so our classes subclass
  **`AsyncScheduler`** (vLLM picks that when `scheduler_cls` is unset; we must
  preserve its `_update_after_schedule` override).

## Files

| Path | What |
|---|---|
| `llumnix_sched.py` | our schedulers (`SJFScheduler`, `SRPFScheduler`) |
| `vendor-reference/sched/` | **read-only copy** of vLLM's `v1/core/sched/` from the image, so the code we subclass can be read/diffed without exec-ing into a pod. Never applied — reference only. |
| `VLLM_VERSION.txt` | exact engine vLLM version the reference was taken from |

## Policy → how it is realised

| Policy | Engine flags | Priority value | Custom class |
|---|---|---|---|
| FIFO | *(none — default)* | — | no |
| EDF | `--scheduling-policy priority` | **client** sets `priority = arrival_ms + SLO_ms` (absolute deadline), gateway forwards it | no |
| SJF | `--scheduling-policy priority --scheduler-cls llumnix_sched.SJFScheduler` | engine sets `priority = num_prompt_tokens` | yes |
| SRPF | `--scheduling-policy priority --scheduler-cls llumnix_sched.SRPFScheduler` | as SJF, **plus** per-step reorder of `running` by remaining prefill | yes |

### Why EDF needs no custom class
An EDF deadline is *absolute* (`arrival + SLO`) and therefore time-invariant, so
a static per-request priority already yields exact EDF ordering. vLLM keeps the
waiting queue as a **live** priority heap (a newly arrived, earlier-deadline
request goes straight to the top) and, every step, preempts
`max(running, key=(priority, arrival_time))` — the latest deadline — when KV is
exhausted. Recomputing a key each step would be *Least-Laxity-First*, a
different algorithm, and is not needed here.

Caveat (verified in `vendor-reference/sched/scheduler.py`): vLLM has **no
"preempt a running request in order to admit a waiting one"** path — the waiting
loop `break`s when KV allocation fails. Room is freed indirectly, by the running
loop preempting the lowest-priority running request when a *running* request
needs to grow. Under sustained overload with active decode (the EXP-15 operating
point) this fires continuously, so urgent arrivals are admitted quickly; a
pure-prefill saturation corner case could delay them.

### Why SJF ≠ SRPF here
A *waiting* request has computed 0 tokens, so its remaining prefill equals its
full prompt — waiting-queue order is identical under both. The difference is
purely dynamic: SRPF additionally reorders the **running** list each step by
remaining prefill so near-complete prefills finish first.

## Deployment

Mount this directory into the engine container and put it on `PYTHONPATH`:

```yaml
volumes:
  - name: llumnix-sched
    hostPath: { path: /home/nxclab/llumnix_reproduce/patches/vllm-sched, type: Directory }
volumeMounts:
  - { name: llumnix-sched, mountPath: /opt/llumnix-sched, readOnly: true }
env:
  - { name: PYTHONPATH, value: /opt/llumnix-sched }
```

then add the per-policy flags to `vllm serve` (table above). Because the mount is
a hostPath, edits here take effect on the next engine restart — which the
experiment driver already does per condition (`--restart-per-condition`).

Revert = drop the flags (the mount is inert without `--scheduler-cls`).

## Sanity check

```bash
kubectl -n llumnix exec neutral-0 -c vllm -- \
  python3 -c "import sys; sys.path.insert(0,'/opt/llumnix-sched'); \
              import llumnix_sched; print(llumnix_sched.SRPFScheduler.__mro__[:3])"
```
Engine startup also logs vLLM's own warning `Using custom scheduler class ...`,
which is the positive confirmation that the class was picked up.

---

---

## DeadlineScheduler (Niyama port — phase 1: no dynamic chunking)

`deadline_sched.py` ports Niyama's (Sarathi-Serve, ASPLOS'26) deadline-aware
scheduler onto stock vLLM V1. Implemented: deadline waiting-order (unit 2),
slack-based reorder of `running` (unit 3), eager relegation (unit 5), **dynamic
prefill chunk sizing (unit 4)** driven by a B200-fit batch-time model (unit 6).
SLA tiers + the TTFT/TTLT deadline model (unit 1) live **client-side** in
`slo_tier.py`. Per-class TBT reaches the engine packed into `priority`
(`slo_ms*1000 + tbt_ms`, phase 1.5) and drives the decode-phase slack per request.

### Launch

```
vllm serve ... --scheduling-policy priority \
               --scheduler-cls deadline_sched.DeadlineScheduler
```

### Client-driven contract (the gateway needs NO change)

The Llumnix path is `/v1/completions`-only, where the gateway forwards `priority`
and `max_tokens` verbatim. So metadata is **client-driven**: the completions
adapter classifies each request (`slo_tier.py`, the user's rule) and sends

  * `priority` = **relative first-token-equivalent SLO in ms** (via
    `slo_tier.priority_for(ttft_slo_ms, e2e_slo_ms, output_len)`), NOT an
    absolute deadline (that is plain EDF's contract).
  * `max_tokens` = target output length (unchanged).

The scheduler stamps engine-clock arrival in `add_request`, turns the relative
SLO into an absolute engine-clock deadline, and overwrites `priority` with it so
the PriorityRequestQueue gives EDF order. **Until the adapter sends
`priority = SLO_ms`, every request falls back to `DEADLINE_DEFAULT_SLO_MS`.**

Only EDF and DeadlineScheduler consume per-request priority. FIFO ignores it;
SJF/SRPF overwrite it with the prompt length inside the engine.

### Tier rule (`slo_tier.py`, client-side, pluggable)

| declares | tier | importance | first-token SLO sent |
|---|---|---|---|
| TTFT SLO | interactive | 2 (highest) | `ttft_slo_ms` |
| E2E SLO only | e2e (TTLT) | 1 | `e2e_slo_ms - output_len*TBT_BATCH` |
| no SLO | besteffort | 0 (lowest) | huge (FCFS) |

Fine-grained "tighter TTFT scheduled sooner" is handled automatically by the
absolute-deadline priority, so the tier stays a coarse 3-class. Swap the rule via
`DEADLINE_SLO_TIER` (registry name or `module:Class`).

### Tunables (env; Niyama A100 defaults — MEASURE on B200)

| env | default | side | meaning |
|---|---|---|---|
| `DEADLINE_SLO_TIER` | `declared` | client | tier classifier |
| `DEADLINE_TBT_BATCH_S` | `0.400` | client | TBT budget for TTLT->TTFT conversion (B200: set ~0.02) |
| `DEADLINE_BESTEFFORT_SLO_MS` | `~30d` | client | best-effort sentinel SLO |
| `DEADLINE_TBT_S` | `0.100` | engine | global TBT for running-queue slack (unit 3) |
| `DEADLINE_RELEGATION` | `1` | engine | unit 5 eager relegation on/off |
| `DEADLINE_PREFILL_TPS` | `8500` | engine | initial prefill-throughput estimate (tok/s) |
| `DEADLINE_DEFAULT_SLO_MS` | `30000` | engine | fallback SLO if adapter sent no priority |
