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


__all__ = ["SJFScheduler", "SRPFScheduler", "remaining_prefill_tokens"]
