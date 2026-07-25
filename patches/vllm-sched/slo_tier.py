"""SLO -> tier classification (the *client-side* rule) for DeadlineScheduler.

Because the deployment is **client-driven** (the completions adapter stamps
per-request scheduling metadata; the gateway just forwards it on the
``/v1/completions`` path), and because vLLM forwards only ONE scalar channel
(``priority``) plus ``max_tokens`` without a source change, the request's *tier*
must be decided where the raw SLOs are visible — the client/adapter — and folded
into a single **relative first-token-equivalent SLO (ms)** that is sent as
``priority``. The engine-side ``DeadlineScheduler`` then consumes that scalar
uniformly (anchor at engine arrival -> absolute deadline -> EDF order + slack).

This module is intentionally **vLLM-free** so the completions adapter (a plain
HTTP client) can import it.

Tier rule (user-specified; a non-random realisation of Niyama's tier, which
bundles {deadline magnitude, SLA type TTFT/TTLT, TBT budget}):

  has TTFT SLO      -> "interactive"  (most important; TTFT SLA, tight TBT)
  E2E SLO only      -> "e2e"          (mid; TTLT SLA -> first-token-equivalent
                                       deadline = e2e - output_len*tbt_batch)
  no SLO            -> "besteffort"   (least important; huge deadline, FCFS)

The fine-grained "tighter TTFT => scheduled sooner" ordering is NOT encoded in
the tier — it falls out of the absolute-deadline priority on the engine side, so
the tier stays a coarse 3-class that only selects SLA semantics / TBT budget.

Swap the rule via env ``DEADLINE_SLO_TIER`` (registry name or ``module:Class``).
"""

import importlib
import os
from dataclasses import dataclass
from typing import Optional


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


# TBT (per-decode-token) SLA budget used for the TTLT->TTFT conversion, seconds.
# B200 (EXP-16): pure-decode step ITL median ~25ms — reserve that per output tok.
TBT_BATCH_S = _env_float("DEADLINE_TBT_BATCH_S", 0.025)
# Sentinel "no deadline" SLO for best-effort requests (ms). Large but finite so
# the priority stays a normal int and best-effort requests still order FCFS.
BESTEFFORT_SLO_MS = _env_float("DEADLINE_BESTEFFORT_SLO_MS", 30 * 24 * 3600 * 1000.0)


@dataclass(frozen=True)
class Tier:
    name: str
    importance: int  # higher = more important (interactive > e2e > besteffort)
    first_token_slo_ms: float  # RELATIVE deadline the adapter sends as `priority`


class SloTierPolicy:
    """Base class. Override ``classify``."""

    def classify(
        self,
        ttft_slo_ms: Optional[float],
        e2e_slo_ms: Optional[float],
        output_len: int,
    ) -> Tier:
        raise NotImplementedError


class DeclaredSloTierPolicy(SloTierPolicy):
    """Default: tier by WHICH SLO the request declares (the user's rule)."""

    def classify(self, ttft_slo_ms, e2e_slo_ms, output_len) -> Tier:
        if ttft_slo_ms is not None:
            # interactive: deadline is the first token; sent as-is.
            return Tier("interactive", importance=2, first_token_slo_ms=float(ttft_slo_ms))
        if e2e_slo_ms is not None:
            # e2e-only (TTLT): reserve decode time so the *first-token* deadline
            # leaves room to emit output_len tokens at the batch TBT budget.
            eq = float(e2e_slo_ms) - output_len * TBT_BATCH_S * 1000.0
            return Tier("e2e", importance=1, first_token_slo_ms=max(1.0, eq))
        # no SLO: best-effort.
        return Tier("besteffort", importance=0, first_token_slo_ms=BESTEFFORT_SLO_MS)


_REGISTRY = {
    "declared": DeclaredSloTierPolicy,
}


def get_slo_tier_policy() -> SloTierPolicy:
    spec = os.environ.get("DEADLINE_SLO_TIER", "declared")
    if spec in _REGISTRY:
        return _REGISTRY[spec]()
    if ":" in spec:
        mod_name, cls_name = spec.split(":", 1)
        return getattr(importlib.import_module(mod_name), cls_name)()
    return DeclaredSloTierPolicy()


def priority_for(
    ttft_slo_ms: Optional[float],
    e2e_slo_ms: Optional[float],
    output_len: int,
    policy: Optional[SloTierPolicy] = None,
) -> int:
    """Convenience for the adapter: classify -> the integer ``priority`` value
    (relative first-token-equivalent SLO in ms) to put on the request body."""
    policy = policy or get_slo_tier_policy()
    tier = policy.classify(ttft_slo_ms, e2e_slo_ms, output_len)
    return int(round(tier.first_token_slo_ms))
