"""Custom vLLM V1 schedulers for the EXP-15 scheduling-policy study.

Injected via vLLM's public-ish ``--scheduler-cls`` hook, so **no vLLM source
file is modified**. The module is mounted into the engine container and put on
PYTHONPATH; the engine is launched with e.g.

    vllm serve ... --scheduling-policy priority \
                   --scheduler-cls llumnix_sched.SRPFScheduler

Base class is ``AsyncScheduler`` (not ``Scheduler``) because the deployment runs
with ``--async-scheduling``; vLLM would otherwise pick AsyncScheduler itself and
we must preserve that behaviour (it overrides ``_update_after_schedule``).

Policies
--------
FIFO   : stock vLLM (``--scheduling-policy fcfs``), no class here.
EDF    : stock vLLM (``--scheduling-policy priority``), no class here — the
         CLIENT sets ``priority = absolute deadline (ms)`` per request and the
         gateway forwards it. Absolute deadlines are time-invariant, so a static
         priority is exactly EDF; vLLM re-evaluates the live priority heap for
         admission and preempts ``max(running, key=(priority, arrival_time))``
         (latest deadline) under KV pressure every step.
SJF    : ``SJFScheduler`` — priority = INITIAL prompt length (static). Shortest
         job first on the waiting queue.
SRPF   : ``SRPFScheduler`` — SJF's waiting order PLUS, every step, the running
         list is reordered by REMAINING prefill ascending so the request closest
         to finishing its prefill gets token budget first (dynamic; this is the
         only thing that distinguishes SRPF from SJF, because a *waiting*
         request has computed 0 tokens and therefore remaining == full prompt).

Both SJF and SRPF require ``--scheduling-policy priority`` so that vLLM uses its
PriorityRequestQueue for the waiting queue; we only supply the priority value.
"""

import json
import os
import threading
import time
from collections import deque

from vllm.logger import init_logger
from vllm.v1.core.sched.async_scheduler import AsyncScheduler
from vllm.v1.request import Request

logger = init_logger(__name__)


def remaining_prefill_tokens(request: Request) -> int:
    """Prompt tokens not yet computed. 0 once the request is decoding."""
    return max(0, request.num_prompt_tokens - request.num_computed_tokens)


class SJFScheduler(AsyncScheduler):
    """Shortest-Job-First: static priority = initial prompt length.

    vLLM's PriorityRequestQueue orders by ``(priority, arrival_time)`` and
    preempts the highest-priority *value* (= longest prompt) first, so setting
    priority to the prompt length yields shortest-first admission and
    longest-first preemption.
    """

    def add_request(self, request: Request) -> None:
        request.priority = request.num_prompt_tokens
        super().add_request(request)


class SRPFScheduler(SJFScheduler):
    """Shortest-Remaining-Prefill-First (dynamic).

    Waiting-queue order is inherited from SJF (a waiting request's remaining
    prefill *is* its full prompt length). The dynamic part: before each
    scheduling pass, reorder ``self.running`` by remaining prefill ascending.
    The running loop hands out the per-step token budget in list order, so
    near-complete prefills finish (and enter decode) sooner, which is the
    classic SRPT effect on mean latency.

    Decode-phase requests have remaining == 0 and therefore sort first; each
    needs only ~1 token per step, so this does not starve prefill.
    """

    def schedule(self):
        # Reorder in place; the running loop iterates self.running by index and
        # may mutate it (preemption) during iteration, which is unaffected by
        # having sorted it beforehand.
        self.running.sort(key=remaining_prefill_tokens)
        return super().schedule()


class EDFDebugScheduler(AsyncScheduler):
    """EDF with logging — verification aid, not an experiment policy.

    Behaviourally identical to stock vLLM under ``--scheduling-policy
    priority`` (it adds no ordering logic of its own; the priority comes from
    the client as an absolute deadline). It only logs the first few priorities
    it receives, which is how we prove end-to-end that the gateway actually
    forwards the client's ``priority`` field instead of silently dropping it.
    """

    _logged = 0
    _LOG_LIMIT = 8

    def add_request(self, request: Request) -> None:
        if EDFDebugScheduler._logged < EDFDebugScheduler._LOG_LIMIT:
            EDFDebugScheduler._logged += 1
            logger.info(
                "[edf-debug] req=%s priority=%s prompt_tokens=%d",
                request.request_id, request.priority, request.num_prompt_tokens,
            )
        super().add_request(request)


class _StepLogger:
    """Background JSONL sink for per-step records.

    The scheduler hot path only does ``buf.append(row)`` (a cheap deque op);
    a daemon thread drains the buffer to disk every second so no file I/O ever
    blocks ``schedule()``. Records still buffered when the pod dies (<=1s worth)
    are lost, which is fine for an aggregate-statistics measurement.
    """

    def __init__(self, path: str):
        self.path = path
        self.buf: "deque[dict]" = deque()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("[instrumented] step log -> %s", path)

    def emit(self, row: dict) -> None:
        self.buf.append(row)

    def _run(self) -> None:
        f = open(self.path, "a", buffering=1)
        while True:
            time.sleep(1.0)
            for _ in range(len(self.buf)):
                try:
                    row = self.buf.popleft()
                except IndexError:
                    break
                f.write(json.dumps(row))
                f.write("\n")
            f.flush()


class InstrumentedScheduler(AsyncScheduler):
    """FIFO-equivalent scheduler (stock AsyncScheduler ordering) that logs the
    composition and timing of every scheduling step, to decompose per-step
    decode latency into T_schedule / prefill / KV / batch terms.

    Ordering behaviour is IDENTICAL to stock vLLM (fcfs) — this class adds no
    scheduling logic, only measurement — so it isolates the engine *physics*
    rather than any policy effect. Enable by setting the env var
    ``SCHED_STEP_LOG`` to a writable path; if unset, overhead is zero and the
    class degenerates to plain AsyncScheduler.

    Per-step record (one JSON line per ``schedule()`` call):
      step                 monotonic step counter
      t_wall               wall-clock seconds (for window alignment)
      t_schedule_us        wall time INSIDE super().schedule()  -> T_schedule
      interval_ms          enter-to-enter gap from previous step -> ground-truth
                           step ITL (== max(T_schedule, T_forward) under async
                           scheduling, free of client/network/SSE jitter)
      kv_tokens            sum of num_computed_tokens over running (batch KV)
      n_running/n_waiting  batch size / waiting-queue depth
      n_decode             running requests past prefill (each ~1 token/step)
      n_prefill_reqs       requests still in (chunked) prefill this step
      prefill_tokens_step  scheduled tokens belonging to prefill  -> P_tokens
      total_sched_tokens   SchedulerOutput.total_num_scheduled_tokens

    Regressing interval_ms on (kv_tokens, n_decode, prefill_tokens_step) with
    t_schedule_us measured directly answers whether each term EXISTS and is
    separable — the modelling-feasibility question — before fitting coefficients.
    """

    _logger = None
    _last_enter_ns = None
    _step = 0

    def _get_logger(self):
        if InstrumentedScheduler._logger is None:
            path = os.environ.get("SCHED_STEP_LOG")
            if not path:
                return None
            InstrumentedScheduler._logger = _StepLogger(path)
        return InstrumentedScheduler._logger

    def schedule(self):
        lg = self._get_logger()
        if lg is None:
            return super().schedule()

        t0 = time.perf_counter_ns()
        # Pre-step snapshot: (computed, prompt_len) per running request. A
        # request scheduled this step that is NOT in this snapshot was just
        # admitted from the waiting queue, hence in prefill.
        try:
            snap = {r.request_id: (r.num_computed_tokens, r.num_prompt_tokens)
                    for r in self.running}
            kv_tokens = sum(c for c, _ in snap.values())
            n_running = len(self.running)
            n_waiting = len(self.waiting)
        except Exception:                       # never break scheduling
            snap, kv_tokens, n_running, n_waiting = {}, -1, -1, -1

        out = super().schedule()
        t1 = time.perf_counter_ns()

        try:
            nst = getattr(out, "num_scheduled_tokens", None) or {}
            p_tokens = n_decode = n_prefill = 0
            for rid, ntok in nst.items():
                s = snap.get(rid)
                if s is None or s[0] < s[1]:    # newly admitted, or comp<prompt
                    p_tokens += ntok
                    n_prefill += 1
                else:
                    n_decode += 1
            total = getattr(out, "total_num_scheduled_tokens", None)
            if total is None:
                total = sum(nst.values())
        except Exception:
            p_tokens = n_decode = n_prefill = total = -1

        last = InstrumentedScheduler._last_enter_ns
        interval_ms = (t0 - last) / 1e6 if last is not None else None
        InstrumentedScheduler._last_enter_ns = t0
        InstrumentedScheduler._step += 1

        lg.emit({
            "step": InstrumentedScheduler._step,
            "t_wall": time.time(),
            "t_schedule_us": (t1 - t0) / 1e3,
            "interval_ms": interval_ms,
            "kv_tokens": kv_tokens,
            "n_running": n_running,
            "n_waiting": n_waiting,
            "n_decode": n_decode,
            "n_prefill_reqs": n_prefill,
            "prefill_tokens_step": p_tokens,
            "total_sched_tokens": total,
        })
        return out


__all__ = [
    "SJFScheduler",
    "SRPFScheduler",
    "EDFDebugScheduler",
    "InstrumentedScheduler",
    "remaining_prefill_tokens",
]
