# EXP-61 — a deadline-aware engine scheduler under a router that leaves it work

**Written after the run, from the chain script's header.** Same omission as
EXP-60 and recorded for the same reason.

## 1. Why

EXP-40 crossed two control planes with two engine schedulers and found the gap
between the control planes unchanged. Its weakness is now measured rather than
argued. Per-engine waiting requests at 60 req/s, median/max, from the engines'
own metrics:

| | four engines |
|---|---|
| **Llumnix load balance** | **373/1559, 325/1611, 367/1496, 404/1647** |
| Llumnix SLO | 0/47, 0/17, 0/20, 0/54 |
| FluidServe | 0/11, 0/10, 0/11, 0/10 |

Both control planes EXP-40 used keep the engine queues empty — FluidServe by
holding requests at the gateway, Llumnix SLO by rejecting 49.9% of arrivals at
that rate — so three of Niyama's five units had nothing to act on and
preemptions were zero in all 24 of its conditions.

Plain load balancing rejects nothing and holds nothing. It is the only arm where
a deadline-aware engine scheduler has the work it exists for, and it is the arm
EXP-40 left out. The objection this answers is the strong form: **put the best
engine scheduler under a naive router and see how close it gets.**

## 2. Design

`loadbalance` × {stock FIFO, DeadlineScheduler}, 45 / 50 / 60 req/s, two
repeats, one session, eight minutes per condition. Both cells run here rather
than taking the FIFO one from EXP-53, because cross-session movement on this
workload reaches 4.6 points. Rates chosen so there is room above the floor:
load balancing reads 32.1 / 14 / 7 offered at those rates, and at 70 it is at
3.9 where a floor effect could hide an improvement.

**Prediction**: the deadline scheduler improves attainment here, because unlike
in EXP-40 there is a queue to reorder. **Refutation**: no improvement beyond the
repeat spread, which would say the engine layer cannot recover what the routing
layer gave away even when it has the work.

## 3. Result — refuted, and slightly the other way

| rate | FIFO | QoServe | difference | repeat spread | goodput |
|---|---|---|---|---|---|
| 45 | 30.71 | 29.57 | −1.14 | 1.16 | 8,245 → 8,016 |
| 50 | 14.39 | 13.45 | **−0.93** | **0.29** | 3,323 → **2,997** |
| 60 | 7.18 | **6.73** (one repeat) | **−0.45** | — | 1,485 → **1,281** |

**All three rates get slightly worse and goodput falls at all three** (−3, −10,
−14%). At 50 req/s the difference exceeds the repeat spread, so that row is not
noise.

⚠ **One of the two QoServe repeats at 60 req/s is excluded**: the stack, not the
policy, failed in it. Load balance has no admission control, yet that condition
reported a 22.6% rejection rate; the gateway returned 400 from minute 3 and 503
from minute 7, and the runner labels a 503 `no available inference worker` as
`KV_THRESHOLD`, which is not our KV admission threshold. The other three
conditions at this rate have zero of both errors. Account in
`fluidserve-implementation.md` §63.6.1; the excluded run is listed with its
reason in `ms_dev/notes/excluded_runs.tsv`. With it included the mean was 6.08,
the difference −1.10 and goodput 1,265, so the direction and the conclusion are
unchanged and only the magnitude moves.

**Reading**: ordering by deadline chooses an order *within the set that can
still be met*, and when arrivals are about twice capacity that set is nearly
empty — a median of 325 to 404 waiting requests per engine is not an opportunity
to reorder, it is evidence that everything is late. The goodput loss is
consistent with dynamic chunking's throughput cost (§35.3 measured −3.6% total
throughput) being paid without the corresponding gain.

⚠ At 60 req/s FIFO is already at 7.2, so a floor effect is possible there. The
conclusion rests on 45 req/s (30.7, ample room) and on 50 req/s (repeat spread
0.29).

## 4. What it does not settle

- One engine scheduler, and the port reduces to one live unit under policies
  that empty the queue — but here the queue is full, so units 2, 3 and 5 did
  have work. Whether they ran as the original intends is a fidelity question
  (`qoserve-niyama-fidelity.md`), not answered here.
- PolyServe is the other candidate arm and was not run: it rejects nothing and
  accumulates 3,679 waiting requests on **one** engine while three sit at zero,
  which is where reordering alone can be measured against placement being wrong.
