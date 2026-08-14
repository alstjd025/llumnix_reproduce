"""``DeadlineScheduler`` — a port of Niyama's (Sarathi-Serve, ASPLOS'26)
deadline-aware scheduler onto stock vLLM V1, phase 1: **no dynamic chunking**.

This is the ``deadline_no_dynamic_chunking`` variant from the paper, chosen first
to isolate the one uncertain mechanism (dynamic prefill chunk sizing, "unit 4")
into a later phase. Implemented here (Niyama-unit -> where):

  unit 1  SLA tier + TTFT/TTLT deadline model  -> CLIENT adapter (slo_tier.py)
  unit 2  deadline ordering of the waiting queue -> request.priority (EDF heap)
  unit 3  slack-based reordering of running     -> schedule() pre-sort
  unit 5  eager relegation of doomed requests   -> schedule() priority bump

  unit 4  dynamic chunk sizing                  -> _budget_by_slack (PHASE 2, LANDED)

  unit 6  linear batch-time predictor           -> NOT PORTED. Niyama needs it to
          predict a batch's time so unit 4 can size the chunk; _budget_by_slack
          reaches the same decision from the tightest running decode's remaining
          slack instead, so nothing here calls for it.

NOTE, 2026-08-14: the two lines above used to say unit 4 was "NOT YET (phase 2)"
long after phase 2 had landed, and EXP-81 was planned on that stale claim -- its
section 5 said the experiment measured a port with unit 4 missing when in fact
all four implemented units were running. The start-up line has printed
`dynamic_chunk=True` the whole time and ms_dev/notes/qoserve-niyama-fidelity.md
has a section on unit 4; the header was the only thing that disagreed. A comment
that describes what the file does not do is the kind that goes stale silently.

No vLLM source is modified; loaded via ``--scheduler-cls deadline_sched.DeadlineScheduler``
with ``--scheduling-policy priority`` (so the waiting queue is a PriorityRequestQueue).

Deployment is **client-driven**: the completions adapter classifies each request
into a tier and folds it into a single relative first-token-equivalent SLO (see
``slo_tier.py``), which it sends as ``priority`` on the ``/v1/completions`` body.
This scheduler therefore does NOT classify tiers itself — it treats ``priority``
uniformly as:

    priority (in)  = RELATIVE first-token-equivalent SLO, milliseconds
                     (NOT an absolute deadline — that's plain EDF's contract)

Because the engine clock (``time.monotonic``) differs from the client wall clock,
``add_request`` stamps the engine-clock arrival, turns the relative SLO into an
absolute engine-clock deadline, and OVERWRITES ``request.priority`` with that
deadline (ms) so the PriorityRequestQueue still yields EDF admission order. All
dynamic state lives in ``self._meta`` keyed by request id; the vLLM ``Request``
object is never mutated beyond ``priority``.

Phase-1 simplification: a single global TBT budget is used for the running-queue
slack (unit 3). Per-tier TBT (Niyama's interactive-vs-batch distinction) needs a
second channel to reach the engine and is deferred to phase 1.5.
"""

import os
import time

from vllm.logger import init_logger
from vllm.v1.core.sched.async_scheduler import AsyncScheduler
from vllm.v1.request import Request

logger = init_logger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").lower() in ("1", "true", "yes")


# Fallback SLO if the adapter forwarded no priority (ms).
DEFAULT_SLO_MS = _env_float("DEADLINE_DEFAULT_SLO_MS", 30_000.0)
# Global per-decode-token SLA budget for running-queue slack (seconds).
# B200 (EXP-16): pure-decode step ITL median 25ms / p90 47ms; 0.05 ~ p90 budget.
TBT_S = _env_float("DEADLINE_TBT_S", 0.050)
# unit 4: dynamic prefill chunk sizing (slack-driven). Batch-time model fit on
# B200 EXP-16 sched_steps.jsonl: interval_ms ~ C0 + C_kv*kv + C_pt*prefill_tok +
# C_nd*n_decode (kv coef = 30.6ms/Mtok, prefill = ~32k tok/s — matches physics;
# R2~0.35 due to async max()/tail, so used as a central estimate + safety).
DYNAMIC_CHUNK = _env_flag("DEADLINE_DYNAMIC_CHUNK", True)
_C0 = _env_float("DEADLINE_BT_C0", 18.889)          # ms base per step
_C_KV = _env_float("DEADLINE_BT_CKV", 3.058e-05)    # ms per KV token
_C_PT = _env_float("DEADLINE_BT_CPT", 3.087e-02)    # ms per prefill token
_C_ND = _env_float("DEADLINE_BT_CND", 3.170e-02)    # ms per decode request
_CHUNK_SAFETY = _env_float("DEADLINE_CHUNK_SAFETY", 1.2)   # Niyama pred_thres
_MIN_PREFILL = int(_env_float("DEADLINE_MIN_PREFILL", 128))  # never fully stall
# Cost of the already-computed prefix a prefilling request carries, per token of
# that prefix. NOT fitted here: our per-step captures have no prefill-token
# signal, which is the same gap that stops the decode law being corrected online
# (see the note above newCapacityModel). Transferred from Niyama's own
# >512-token model, where the prefill-context coefficient is 2.727223e-6 s/token
# against 6.863043e-5 s/token for prefill tokens, a ratio of 0.0397; that ratio
# is applied to our fitted _C_PT. Hardware-independent only to the extent that
# the ratio of two attention costs is, so it is a stated estimate and not a
# measurement. Set DEADLINE_BT_CPCTX=0 to recover the previous behaviour, which
# priced prefill tokens and ignored the prefix they attend over.
_C_PCTX = _env_float("DEADLINE_BT_CPCTX", 3.087e-02 * 0.0397)  # ms per prefix tok
# Niyama searches a fixed grid and never schedules a step larger than its top
# entry, 2552 total tokens (deadline_scheduler.py TOKEN_SEARCH_SPACE). The port
# previously clamped to the engine's max_num_batched_tokens, 8192 here, which
# lets a single step prefill a whole 5.5k-token agent prompt -- about 436 ms, the
# tail that makes the observed iteration time bimodal (implementation.md 29).
# Niyama would spread the same prompt over at least three steps.
_GRID = [128] + list(range(256, 2049, 256)) + [2552]
_GRID_LOW_MEM = list(range(128, 513, 128))
# Niyama's low-memory branch: free KV tokens // 32 below this switches to the
# restricted grid, so the next few steps cannot exhaust the pool.
_LOW_MEM_TOKENS = _env_float("DEADLINE_LOW_MEM_TOKENS", 1000.0)
USE_GRID = _env_flag("DEADLINE_USE_GRID", True)
# unit 2: Niyama's `deadline` policy is NOT pure EDF. Sequence.__lt__ orders by
# `ttft_deadline + arrival_time + param * remaining_prefill_tokens` with
# param = 0.008 s/token, a deliberate blend of EDF and shortest-remaining-prefill
# that keeps a long prompt from blocking the head of the queue. `edf` and `srpf`
# are separate scheduler_type values there. Set to 0 for pure EDF.
# Niyama's 0.008 is added to values in SECONDS, so it is 8 ms per token, not per
# thousand. On our mix that is 5.2 s for a 649-token chat prompt and 44.5 s for a
# 5,557-token agent prompt, against deadlines of 5 s and 11.8 s -- the term does
# not adjust the order, it dominates it, and `deadline` is in practice
# shortest-prompt-first with the deadline as a tiebreak. Kept at the paper value.
_HYBRID_PARAM_MS = _env_float("DEADLINE_HYBRID_PARAM_MS", 8.0)  # ms per token
# Token budget reserved per running decode so shrinking the step budget never
# starves a decode of its 1 token (async scheduling can ask for 2).
_DECODE_RESERVE = int(_env_float("DEADLINE_DECODE_RESERVE", 2))
# unit 5: cap how many waiting requests relegation inspects per step. The full
# queue is O(n log n) to iterate (PriorityRequestQueue.__iter__ copies + drains
# the heap), which is unaffordable in the scheduling hot path once a backlog
# forms; only the near-head entries can be admitted soon anyway.
_RELEGATE_SCAN = int(_env_float("DEADLINE_RELEGATE_SCAN", 32))
# unit 5: eager relegation on/off + initial prefill-throughput estimate (tok/s).
RELEGATION_ENABLED = _env_flag("DEADLINE_RELEGATION", True)
# B200 (EXP-16): effective prefill throughput ~10.5k tok/s (total prefill tok /
# total prefill-step time across chat+swe rate sweeps).
INIT_PREFILL_TPS = _env_float("DEADLINE_PREFILL_TPS", 10500.0)
_TPS_DECAY = 0.995  # EMA weight on the running estimate

_RELEGATED_PRIORITY = 10**15  # push doomed requests to the back of the heap


def _remaining_prefill(request: Request) -> int:
    return max(0, request.num_prompt_tokens - request.num_computed_tokens)


def _output_tokens(request: Request) -> int:
    """Decode tokens produced so far (0 while still prefilling)."""
    return max(0, request.num_computed_tokens - request.num_prompt_tokens)


class DeadlineScheduler(AsyncScheduler):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._meta: dict[str, dict] = {}
        # aggregate token-throughput EMA, used by relegation's completion estimate
        self._prefill_tps = INIT_PREFILL_TPS
        self._last_sched_t = None
        self._last_prefill_tokens = 0
        self._logged = 0
        # Save the engine's static chunk knobs; unit 4 mutates them per step and
        # restores these as the "no constraint" / ceiling values.
        self._orig_long_prefill = self.scheduler_config.long_prefill_token_threshold
        self._orig_max_batched = self.max_num_scheduled_tokens
        logger.info(
            "[deadline] relegation=%s dynamic_chunk=%s grid=%s grid_top=%d "
            "hybrid_ms_per_tok=%.3f c_pctx=%.3e tbt_s=%.3f init_tps=%.0f "
            "max_batched=%d",
            RELEGATION_ENABLED, DYNAMIC_CHUNK, USE_GRID, _GRID[-1],
            _HYBRID_PARAM_MS, _C_PCTX, TBT_S, INIT_PREFILL_TPS,
            self._orig_max_batched,
        )

    # ---- unit 2: anchor the relative SLO to engine clock, set EDF order ----
    def add_request(self, request: Request) -> None:
        now = time.monotonic()

        # priority carries a packed value: slo_ms*1000 + tbt_ms (phase 1.5).
        # The client (completions adapter) folds the tier's first-token SLO and
        # per-class TBT budget into this scalar; unpack both here.
        raw_priority = getattr(request, "priority", None)
        if raw_priority:
            enc = int(raw_priority)
            tbt_ms = enc % 1000
            slo_ms = float(enc // 1000)
            tbt_s = tbt_ms / 1000.0 if tbt_ms > 0 else TBT_S
        else:
            slo_ms = DEFAULT_SLO_MS
            tbt_s = TBT_S
        ttft_deadline = now + slo_ms / 1000.0  # absolute, engine clock

        self._meta[request.request_id] = {
            "ttft_deadline": ttft_deadline, "tbt_s": tbt_s, "drop": 0,
        }
        # unit 2: Niyama's hybrid prioritization, not plain EDF. The remaining
        # prefill of a WAITING request is its whole prompt (nothing computed
        # yet), so the term is fixed at admission and the priority stays static,
        # which is what vLLM's live heap needs -- recomputing a key per step
        # would be least-laxity-first, a different algorithm.
        hybrid = _HYBRID_PARAM_MS * float(request.num_prompt_tokens)
        request.priority = int(ttft_deadline * 1000.0 + hybrid)

        if self._logged < 8:
            self._logged += 1
            logger.info(
                "[deadline] req=%s slo_ms=%.0f tbt_ms=%.0f ttft_deadline=+%.3fs",
                request.request_id, slo_ms, tbt_s * 1000, ttft_deadline - now,
            )
        super().add_request(request)

    # ---- unit 3: reorder running by slack (decodes first, then prefills) ---
    def _sort_key(self, request: Request, now: float):
        m = self._meta.get(request.request_id)
        if m is None:  # request we never saw in add_request (shouldn't happen)
            return (1, request.priority)
        if _remaining_prefill(request) > 0:
            # still prefilling: order by (first-token) deadline, earliest first;
            # prefills sit AFTER decodes so V1's tail-preemption sheds a doomed
            # prefill before it starves an active decode's TBT.
            return (1, m["ttft_deadline"])
        # decoding: slack against the per-token (TBT) deadline; least slack first
        # => most-urgent decode gets budget first and is preempted last. Uses the
        # request's own per-class TBT budget (phase 1.5), not a global constant.
        tbt_deadline = m["ttft_deadline"] + m["tbt_s"] * (_output_tokens(request) + 1)
        return (0, tbt_deadline - now)

    # ---- unit 5: eager relegation of requests that cannot meet their deadline
    def _update_tps(self, now: float) -> None:
        """EMA of PREFILL throughput, in tokens per second.

        Niyama accumulates `last_prefill_size` -- the prefill tokens it actually
        scheduled -- and divides by the elapsed time
        (deadline_scheduler.py, `expected_prefill_throughput`). This previously
        differenced `sum(num_computed_tokens)` over all running requests, which
        counts decode tokens too: at our operating point roughly 250 decode
        against 800 prefill tokens per step, so the estimate ran about 30% high
        and relegation fired later than the original's would.
        """
        if self._last_sched_t is not None:
            dt = now - self._last_sched_t
            if dt > 1e-4 and self._last_prefill_tokens > 0:
                obs = self._last_prefill_tokens / dt
                self._prefill_tps = _TPS_DECAY * self._prefill_tps + (1 - _TPS_DECAY) * obs
        self._last_sched_t = now
        self._last_prefill_tokens = 0

    def _relegate_waiting(self, now: float) -> None:
        """Bump the priority of any WAITING request whose prefill cannot finish
        before its first-token deadline, so V1 admits it only after feasible
        requests. Mirrors Niyama's ``drop`` flag (deadline_scheduler.py L300)."""
        wq = self.waiting
        # only meaningful on the priority heap; needs re-heapify after a mutation
        if not hasattr(wq, "remove_request") or not hasattr(wq, "add_request"):
            return
        tps = max(1.0, self._prefill_tps)
        # Only inspect the near-head entries: iterating the whole queue is
        # O(n log n) per step (heap copy + full drain) and a backlog makes that
        # dominate the scheduling hot path. The heap array's leading entries are
        # the ones closest to admission, which is all relegation can act on.
        heap = getattr(wq, "_heap", None)
        candidates = heap[:_RELEGATE_SCAN] if heap is not None else []
        doomed = []
        for req in candidates:
            m = self._meta.get(req.request_id)
            if m is None or m["drop"]:
                continue
            service_s = _remaining_prefill(req) / tps
            if now + service_s > m["ttft_deadline"]:
                doomed.append(req)
        for req in doomed:
            try:
                wq.remove_request(req)
            except (ValueError, KeyError):
                continue
            self._meta[req.request_id]["drop"] = 1
            req.priority = _RELEGATED_PRIORITY + req.priority
            wq.add_request(req)

    # ---- unit 4: dynamic prefill chunk sizing driven by the tightest decode
    def _budget_by_slack(self, now: float) -> int:
        """Total step token budget so the batch-time prediction still fits the
        tightest running decode's TBT slack.

        Niyama bounds the *sum* of prefill tokens scheduled in a step
        (``prefill_token_limit``). The V1 equivalent is the step token budget
        (``max_num_scheduled_tokens``) — NOT ``long_prefill_token_threshold``,
        which is a PER-REQUEST clamp: lowering that does not reduce the prefill
        work, it lets the waiting loop admit budget/clamp requests per step
        (8192/128 = 64), exploding the running set and KV until the engine
        preemption-thrashes. Measured directly in EXP-17 (running p90 223 vs
        FIFO 87, KV 100%, 2783 preemptions), so we bound the total instead and
        leave the per-request clamp at the engine's configured value.
        """
        kv = 0
        ndec = 0
        min_slack_ms = float("inf")
        for r in self.running:
            if _remaining_prefill(r) > 0:
                continue  # a prefill, not a decode
            m = self._meta.get(r.request_id)
            if m is None:
                continue
            kv += r.num_computed_tokens
            ndec += 1
            tbt_deadline = m["ttft_deadline"] + m["tbt_s"] * (_output_tokens(r) + 1)
            min_slack_ms = min(min_slack_ms, (tbt_deadline - now) * 1000.0)

        grid = _GRID
        if self._free_kv_tokens() / 32.0 < _LOW_MEM_TOKENS:
            # Niyama's memory branch: with little free KV, cap the step so the
            # next few steps cannot exhaust the pool.
            grid = _GRID_LOW_MEM
        ceiling = min(self._orig_max_batched, grid[-1]) if USE_GRID \
            else self._orig_max_batched

        if ndec == 0:
            return int(ceiling)  # no decode to protect -> full budget

        # Prefix the prefilling requests already carry. Niyama estimates this
        # from the head of its prefill queue and calls it a conservative
        # estimate; the V1 equivalent is the largest computed prefix among the
        # requests currently prefilling, plus the head of the waiting queue if
        # one is about to be admitted.
        pctx = self._prefill_context_estimate()

        def predict(prefill_tokens: float) -> float:
            return (_C0 + _C_KV * kv + _C_ND * ndec
                    + _C_PT * prefill_tokens + _C_PCTX * pctx)

        if not USE_GRID:
            headroom_ms = min_slack_ms - (_C0 + _C_KV * kv + _C_ND * ndec
                                          + _C_PCTX * pctx) * _CHUNK_SAFETY
            max_prefill = headroom_ms / _C_PT if _C_PT > 0 else ceiling
            total = ndec * _DECODE_RESERVE + max(_MIN_PREFILL, min(max_prefill, ceiling))
            return int(max(_MIN_PREFILL, min(total, ceiling)))

        # Niyama's binary search over the grid: the largest TOTAL token count
        # whose predicted time, inflated by pred_thres, still fits the tightest
        # decode's slack. The grid entry is a total, so the prefill share is what
        # remains after the decodes have drawn their tokens.
        best = grid[0]
        if min_slack_ms > 0:
            lo, hi = 0, len(grid) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                total_tokens = grid[mid]
                prefill_tokens = max(0.0, total_tokens - ndec * _DECODE_RESERVE)
                if predict(prefill_tokens) * _CHUNK_SAFETY <= min_slack_ms:
                    best = total_tokens
                    lo = mid + 1
                else:
                    hi = mid - 1
        total = ndec * _DECODE_RESERVE + max(
            _MIN_PREFILL, best - ndec * _DECODE_RESERVE)
        return int(max(_MIN_PREFILL, min(total, ceiling)))

    def _free_kv_tokens(self) -> float:
        """Free KV capacity in tokens, or +inf when the accessor is not there.

        Returning infinity rather than zero on an unknown API keeps the
        low-memory branch off by default: a wrong clamp to 512 tokens would
        throttle prefill on every step and would be hard to distinguish from the
        mechanism under test.
        """
        for path in (("kv_cache_manager", "block_pool", "get_num_free_blocks"),
                     ("kv_cache_manager", "get_num_free_blocks")):
            obj = self
            for attr in path:
                obj = getattr(obj, attr, None)
                if obj is None:
                    break
            if callable(obj):
                try:
                    blocks = float(obj())
                except Exception:
                    return float("inf")
                bs = getattr(getattr(self, "cache_config", None), "block_size", 0)
                return blocks * float(bs) if bs else float("inf")
        return float("inf")

    def _prefill_context_estimate(self) -> float:
        """Computed prefix carried by whatever will be prefilled this step."""
        best = 0.0
        for r in self.running:
            if _remaining_prefill(r) > 0:
                best = max(best, float(r.num_computed_tokens))
        heap = getattr(self.waiting, "_heap", None)
        if heap:
            head = heap[0]
            best = max(best, float(getattr(head, "num_computed_tokens", 0)))
        return best

    def schedule(self):
        now = time.monotonic()
        self._update_tps(now)
        if RELEGATION_ENABLED:
            self._relegate_waiting(now)
        # unit 3: SRPF-style in-place reorder, but keyed on deadline slack.
        self.running.sort(key=lambda r: self._sort_key(r, now))
        # unit 4: shrink the per-step TOTAL token budget so prefill never blows
        # the tightest decode's TBT. V1 reads self.max_num_scheduled_tokens fresh
        # into token_budget each schedule() (L224) and asserts against the same
        # attribute (L661), so a per-step overwrite is safe.
        if DYNAMIC_CHUNK:
            self.max_num_scheduled_tokens = self._budget_by_slack(now)
        # Which requests are still prefilling must be read BEFORE the step, since
        # super().schedule() advances num_computed_tokens.
        prefilling = {r.request_id for r in self.running if _remaining_prefill(r) > 0}
        heap = getattr(self.waiting, "_heap", None)
        if heap:
            prefilling.update(r.request_id for r in heap
                              if _remaining_prefill(r) > 0)
        out = super().schedule()
        sched = getattr(out, "num_scheduled_tokens", None) or {}
        self._last_prefill_tokens += sum(
            n for rid, n in sched.items() if rid in prefilling)
        self._prune_meta()
        return out

    def _prune_meta(self) -> None:
        if len(self._meta) > len(self.requests):
            live = set(self.requests.keys())
            self._meta = {k: v for k, v in self._meta.items() if k in live}


__all__ = ["DeadlineScheduler"]
