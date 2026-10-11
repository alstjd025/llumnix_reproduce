# A7: when the class preference frees an engine of chat, is the freed pace used?

**Written 2026-08-24.** This appendix closes the gap that `README.md` section 5 item 1 lists
as the largest of the twenty it records: "no batch size, admission decision, queue depth or
preemption count was read on the freed engine at the moments it was free." It reads exactly
those quantities, on the same four one-hour FluidServe runs the other five appendices use.

## 0. Answer in three lines

The freed engine does less work per second than a chat-carrying engine, and the reason
differs between two regimes that the hour trace contains. In the segment where chat is
93.0 % of arrivals, the freed engine is close to empty: it holds 10.0 to 10.5 decoding
requests against 140.5 to 147.5 on the chat-carrying engines, and its modelled decode step
is 0.217 and 0.219 of the gate it was allowed. In the other three segments the freed engine
is not empty but saturated on memory rather than on pace: its KV cache reads at or above
0.99 for 42 % to 83 % of its seconds, it preempts 21 to 40 times per minute against 0 on
every chat-carrying engine, and the pace headroom the model reports cannot be taken up
because the memory ceiling binds first. Pooled over the hour, the freed engine's modelled
step is 0.416 and 0.417 of its gate while a chat-carrying engine's is 0.764 and 0.769, so by
the policy's own capacity model the loosened pace is used about half as fully as the tight
one.

## 1. Runs, exclusions, and how each quantity was built

| arm | repeat | directory | arrivals | admitted requests joined to an engine |
|---|---|---|---|---|
| preference ON (`fspfx`) | r1 | `results/260822_2141_exp93r1_fspfx_shift` | 98,255 | 79,518 of 79,518 (100.0 %) |
| preference ON (`fspfx`) | r2 | `results/260823_0007_exp93br1_fspfx_shift` | 98,231 | 79,822 of 79,822 (100.0 %) |
| preference OFF (`fsnoaff`) | r1 | `results/260823_0721_exp93nr1_fsnoaff_shift` | 98,240 | 79,162 of 79,162 (100.0 %) |
| preference OFF (`fsnoaff`) | r2 | `results/260823_0834_exp93nbr1_fsnoaff_shift` | 98,247 | 79,523 of 79,523 (100.0 %) |
| llm-d (`llmdslo`), context only | r1 | `results/260822_2303_exp93r1_llmdslo_shift` | — | not joined, see below |
| llm-d (`llmdslo`), context only | r2 | `results/260823_0129_exp93br1_llmdslo_shift` | — | not joined, see below |

No directory whose name contains `PRERUN` exists among the six; the check was run and
returned zero. The reference row this appendix must reproduce is offered per-request
attainment, and it does reproduce: 75.29 and 75.66 with the preference on, 73.93 and 74.19
with it off, on 98,231 to 98,255 arrivals per run.

**Stale instance ids.** `server_metrics/scheduler.jsonl` registers eight instance ids in each
FluidServe run. Four of them report `instance_cms_running_requests` equal to zero in every scrape in which
they appear, 3,726 of 3,726 in each run; they are registrations left behind by an engine restart.
**Four stale ids were excluded per FluidServe run, sixteen in total**, and the four live ids
were mapped to engine ports 8000 to 8003 through the `instance_id` column the runs' own
`analysis/request_engine.csv` carries. In the two llm-d runs **all eight registered ids are
all-zero**, because llm-d does not dispatch through the Llumnix scheduler and its CMS gauges
are never written. The llm-d runs therefore contribute engine-side counters only, and they
appear in section 5 alone.

**Residency.** A request occupies its engine from the client's `start_time` for its
`latency`, which is the definition `a1_separation.md` used. Requests are placed on engines by
`exp41_engine_view.attribute_engines`, which disambiguates the non-unique `(task_id,
call_index)` pair by nearest `start_time` within 2 s; the join matched 100.0 % of admitted
requests in all four runs. Recomputing every window classification with residency starting at
the first token instead of at arrival changes nothing: the chat-free window count is 55, 58,
13 and 14 under both definitions.

**Per-engine work.** `instance_cms_decode_batch_size`, `instance_cms_all_decodes_tokens_num`,
`instance_cms_all_prefills_tokens_num` and `instance_cms_waiting_requests` are gauges, not
counters, and were read per scrape at a 1.00 s median interval and reduced within each window.
`instance_cms_all_decodes_tokens_num` is the logical KV the scheduler attributes to the
decoding requests on that instance, and it is the quantity the decode step law takes.
`instance_cms_kv_cache_usage_ratio_projected` was **not used anywhere**: it is a projection and
it reaches 1.20 in these runs. Physical KV occupancy comes from the engine's own
`vllm:kv_cache_usage_perc`, and output tokens, prompt tokens, preemptions and measured
inter-token latency come from differencing `vllm:generation_tokens_total`,
`vllm:prompt_tokens_total`, `vllm:num_preemptions_total` and
`vllm:inter_token_latency_seconds_sum` divided by `_count` across each window.

**Modelled step.** `step_ms = 16.361 + 1.2822e-5 x kv_tokens + 0.076429 x decode_batch`,
evaluated per scrape and reduced to a window median. An instance's admissible pace is the
minimum nominal budget among the classes resident on it (chat 50 ms, swe 57.7 ms, deep
research 100 ms), and its gate is 0.90 times that pace.

## 2. Question 1 — how many windows are chat-free

Each run contributes 59 non-overlapping 60 s windows on each of four engines, so
**236 (engine, window) pairs per run and 944 across the four runs.** A window is chat-free if
no chat request is resident on that engine at any second of it, chat-carrying if a chat
request is resident at every second, and mixed otherwise.

| arm | repeat | chat-free | chat-carrying | mixed |
|---|---|---|---|---|
| ON | r1 | 55 (23.3 %) | 169 (71.6 %) | 12 |
| ON | r2 | 58 (24.6 %) | 163 (69.1 %) | 15 |
| OFF | r1 | 13 (5.5 %) | 217 (91.9 %) | 6 |
| OFF | r2 | 14 (5.9 %) | 217 (91.9 %) | 5 |

The counts agree with the engine-second measurement `a1_separation.md` reports (25.90 and
28.15 % of engine-seconds looser than 50 ms with the preference on, 6.30 and 6.77 % with it
off); the window rule is stricter than the second rule, so it lands slightly lower.

Per segment, out of 60 (engine, window) pairs in each segment of each run:

| segment | chat share of arrivals | ON r1 | ON r2 | OFF r1 | OFF r2 |
|---|---|---|---|---|---|
| `s0_m2` | 93.0 % | 13 | 13 | 0 | 0 |
| `s1_A` | 33.3 % | 15 | 15 | 7 | 8 |
| `s2_m1` | 76.9 % | 13 | 16 | 1 | 0 |
| `s3_B` | 60.0 % | 14 | 14 | 5 | 6 |

Two structural facts follow. The preference frees an engine in the 93 %-chat segment, where
the feasibility test alone frees none in either repeat. And the freed engine is never idle by
default: only 7 of the 944 windows contain any second with no resident request at all, and
the largest idle fraction in any window is 0.05.

Dropping every window whose centre lies within 180 s of a segment boundary leaves 144 windows
per run and does not move the picture: 29 and 30 chat-free of 144 with the preference on, 7
and 9 of 144 with it off.

## 3. Question 2 — what the freed engine actually does

Whole hour, preference-ON runs, p50 and p90 taken across windows within each class. The
repeat pair is written `r1 / r2`.

| quantity | chat-free p50 | chat-free p90 | chat-carrying p50 | chat-carrying p90 |
|---|---|---|---|---|
| windows in the class | 55 / 58 | — | 169 / 163 | — |
| decode batch size | 135.5 / 132.5 | 157.0 / 167.0 | 162.5 / 170.5 | 243.6 / 233.9 |
| logical KV tokens held | 775,071 / 784,737 | 874,345 / 883,112 | 366,849 / 362,276 | 508,904 / 534,360 |
| physical KV occupancy | 0.995 / 0.995 | 0.999 / 0.999 | 0.430 / 0.435 | 0.461 / 0.464 |
| queued prefill tokens | 33,248 / 40,120 | 100,771 / 99,234 | 0 / 0 | 0 / 0 |
| waiting requests (window p50) | 7.0 / 8.2 | 26.6 / 27.0 | 0.0 / 0.0 | 0.0 / 0.0 |
| waiting requests (window p90) | 17.1 / 15.5 | 30.6 / 31.0 | 1.1 / 1.0 | 3.0 / 2.1 |
| requests routed per second | 1.8 / 1.7 | 2.4 / 2.1 | 6.5 / 6.7 | 11.9 / 11.7 |
| output tokens per second | 1,693 / 1,653 | 2,132 / 2,099 | 3,514 / 3,629 | 5,235 / 5,134 |
| prompt tokens per second | 9,520 / 9,297 | 10,433 / 10,013 | 10,207 / 9,982 | 15,567 / 16,764 |
| modelled decode step (ms) | 36.9 / 36.8 | 39.6 / 40.5 | 34.4 / 34.6 | 38.5 / 38.6 |
| measured inter-token latency (ms) | 68.7 / 69.7 | 72.3 / 74.0 | 46.0 / 46.2 | 49.0 / 48.9 |
| preemptions per minute | 28.5 / 31.5 | 43.9 / 44.0 | 0.0 / 0.0 | 0.0 / 0.0 |
| resident requests | 142.5 / 139.5 | 166.6 / 174.3 | 173.0 / 183.0 | 257.2 / 245.8 |

**The core number: a chat-free engine produces 1,693 and 1,653 output tokens per second
against 3,514 and 3,629 on a chat-carrying engine, which is 48.2 % and 45.5 % as much.** The
repeat spread is 40 tokens per second on the chat-free side and 115 on the chat-carrying
side, so the gap of about 1,850 tokens per second is roughly sixteen times the larger spread.
The freed engine does less work per second, on the most direct measure of work the engines
report.

The freed engine is not, however, doing less of everything. It holds 2.1 times the logical KV,
it is the only place in the fleet with a prefill backlog, it is the only place that preempts,
and its measured inter-token latency is 49 % higher. Its prompt-token rate is within 7 % of a
chat-carrying engine's. What it produces less of is decoded tokens, because each of its
decode slots carries 5,667 and 5,541 logical KV tokens against 2,422 and 2,203 on a
chat-carrying engine.

**The hour average hides two different regimes, and they must be reported separately.**

| quantity, p50 across windows | `s0_m2` (93 % chat) chat-free | `s0_m2` chat-carrying | `s1_A`+`s2_m1`+`s3_B` chat-free | those segments' chat-carrying |
|---|---|---|---|---|
| windows | 13 / 13 | 41 / 41 | 42 / 45 | 128 / 122 |
| resident requests | 11.0 / 11.0 | 151.5 / 160.5 | 142.5 to 152.0 / 135.0 to 156.0 | 152.2 to 204.5 / 150.5 to 205.5 |
| decode batch | 10.5 / 10.0 | 140.5 / 147.5 | 136.0 to 145.2 / 127.0 to 148.5 | 144.8 to 192.5 / 144.5 to 195.5 |
| physical KV occupancy | 0.10 / 0.10 | 0.30 / 0.30 | 1.00 in every segment | 0.4 to 0.5 |
| seconds at KV >= 0.99 | 0 of 780 / 0 of 780 | — | 575 of 900, 414 of 780, 693 of 840 (r1); 741 of 900, 404 of 960, 674 of 840 (r2) | — |
| queued prefill tokens | 0 / 0 | 0 / 0 | 15,637 to 100,486 / 3,289 to 98,569 | 0 |
| output tokens per second | 528 / 514 | 3,764 / 3,629 | 1,786 to 1,894 / 1,675 to 1,786 | 3,020 to 3,938 / 2,975 to 4,122 |
| preemptions per minute | 0.0 / 0.0 | 0.0 / 0.0 | 27.5 to 38.6 / 21.4 to 39.7 | 0.0 |
| modelled decode step (ms) | 17.7 / 17.7 | 30.1 / 30.8 | 36.9 to 38.2 / 35.8 to 38.7 | 33.7 to 37.0 / 33.7 to 36.8 |
| measured inter-token latency (ms) | 19.7 / 19.8 | 40.5 / 39.6 | 68.4 to 70.7 / 67.5 to 71.6 | 44.4 to 48.4 / 44.9 to 48.1 |

In `s0_m2` the freed engine carries 7.0 % and 6.8 % of a chat-carrying engine's decode batch
and produces 14.0 % and 14.2 % of its output tokens, with a KV cache one tenth full, an empty
prefill queue, no waiting requests and no preemption. **That engine is unused.** In the other
three segments the freed engine sits at the physical KV ceiling for 42 % to 83 % of its
seconds and preempts 21 to 40 times per minute while every chat-carrying engine preempts zero
times. That engine is fully committed, but on memory.

## 4. Question 3 — was the loosened pace used?

The ratio is the window's modelled decode step over the gate that window was allowed, where
the gate is 0.90 times the minimum nominal budget among the classes resident. A chat-carrying
window is always gated at 45.0 ms. A chat-free window is gated at 90.0 ms when only deep
research is resident and at 51.9 ms when swe is resident without chat; the median chat-free
gate is 90.0 ms; 11 of the 55 chat-free windows in r1 and 12 of the 58 in r2 are gated at
51.9 ms instead.

| arm, repeat | class | p10 | p25 | **p50** | p75 | p90 | n |
|---|---|---|---|---|---|---|---|
| ON r1 | chat-free | 0.215 | 0.386 | **0.416** | 0.437 | 0.562 | 55 |
| ON r2 | chat-free | 0.218 | 0.359 | **0.417** | 0.439 | 0.493 | 58 |
| ON r1 | chat-carrying | 0.522 | 0.682 | **0.764** | 0.824 | 0.855 | 169 |
| ON r2 | chat-carrying | 0.536 | 0.701 | **0.769** | 0.825 | 0.858 | 163 |
| OFF r1 | chat-free | 0.411 | 0.422 | **0.426** | 0.440 | 0.473 | 13 |
| OFF r2 | chat-free | 0.414 | 0.420 | **0.434** | 0.456 | 0.470 | 14 |
| OFF r1 | chat-carrying | 0.488 | 0.653 | **0.735** | 0.788 | 0.819 | 217 |
| OFF r2 | chat-carrying | 0.481 | 0.639 | **0.732** | 0.786 | 0.821 | 217 |

**A freed engine uses 41.6 % and 41.7 % of the pace it was allowed; a chat-bound engine uses
76.4 % and 76.9 % of the pace it was allowed.** The difference of 35.0 points is about seventy
times the larger repeat spread, which is 0.5 points on the chat-carrying side and 0.1 points
on the chat-free side. By the model the policy uses to make the decision, the loosened pace is
not taken up.

Per segment, the ON-arm chat-free median is 0.217 and 0.219 in `s0_m2`, 0.468 and 0.439 in
`s1_A`, 0.410 and 0.411 in `s2_m1`, and 0.424 and 0.425 in `s3_B`. The `s0_m2` value is the
one that names the problem: an engine allowed a 90.0 ms gate is modelled at 17.7 ms of decode
step. The median ratio of 0.217 is slightly above 17.7/90.0 because 5 of those 13 windows hold
swe and are gated at 51.9 ms.

**The measured pace tells a weaker version of the same story, and the difference between the
two is itself a finding.** Substituting the engine's own measured inter-token latency for the
modelled step gives 0.766 and 0.784 on chat-free windows against 1.021 and 1.026 on
chat-carrying windows. Read this way the freed engine has 22 % of its allowance left while the
chat-bound engines are 2 % past theirs, so the freed pace is partly used and the tight pace is
already missed. The two readings disagree because the decode-only step law under-reads the
freed engine much more than it under-reads a chat-bound engine: the ratio of measured
inter-token latency to modelled step is 1.781 and 1.790 on chat-free windows against 1.326 and
1.314 on chat-carrying ones, and it is 1.113 and 1.107 on the `s0_m2` chat-free windows where
there is no prefill backlog and no preemption. **The model's error is largest exactly where
the policy relies on it to decide that headroom exists.** The gap is consistent with what the
law omits: chunked prefill of long deep research prompts and recompute after preemption both
occupy the engine between decode steps and neither appears in the law.

Three checks. First, replacing the logical KV the scheduler reports with physical KV, taken as
`vllm:kv_cache_usage_perc` times the 585,120-token pool that `plot_kv_flows.py` records for
this deployment, moves the chat-free median from 0.416 to 0.381 and the chat-carrying median
from 0.764 to 0.718, so the ordering and the size of the gap survive the choice of KV
definition. Second, trimming 180 s from each segment boundary gives 0.426 and 0.421 chat-free
against 0.786 and 0.799 chat-carrying. Third, the preference-OFF arm's own chat-free windows,
which number only 13 and 14, sit at 0.426 and 0.434, indistinguishable from the ON arm's
chat-free windows outside `s0_m2`. **The preference does not change what a freed engine does;
it changes how many freed engines there are, and it adds a kind of freed engine that the
feasibility test alone never produces, namely the near-empty one in the 93 %-chat segment.**

## 5. Question 4 — the counterfactual, which is arithmetic and not measurement

**Everything in this section is arithmetic on the deployed step model and on measured window
medians. No run was executed to test it.** The assumption chain is stated first so that each
step can be rejected on its own.

- **A1.** Adding requests to the freed engine leaves its KV per decode slot unchanged at the
  value that engine already shows, `kappa = logical KV / decode batch`. This holds only if the
  added requests have the same context length as those already there.
- **A2.** The deployed decode step law holds at the higher batch, so
  `step(n) = 16.361 + (1.2822e-5 x kappa + 0.076429) x n`.
- **A3.** The pace test binds at 0.90 times the admissible pace, which is 90.0 ms on the
  deep-research-only engine considered here.
- **A4.** At steady state the engine's request rate is `n / D`, where `D` is the mean time a
  request stays resident. `D` is taken from the engine's own observed operating point,
  `D = n_observed / requests_routed_per_second`, which gives 19.4 to 20.3 s in `s0_m2` and
  68.6 to 81.2 s in the other segments.
- **A5.** The logical KV an engine can hold is capped at the value at which its physical KV
  gauge reads 1.00. Measured directly on these runs, that value is 845,628 and 845,981 logical
  tokens, taken as the median over the 27 and 32 windows whose median physical occupancy is at
  least 0.995. This cap exceeds the physical pool because prefix-cache sharing lets two
  requests reference the same block.
- **A6.** Nothing else binds. This is the weakest assumption; preemption, prefill queueing and
  the gateway's own holding are all present on the freed engine and none of them is in the
  model.

| repeat, segment | observed batch | `kappa` (KV tokens per slot) | batch allowed by the 90 ms gate | batch allowed by the KV cap | fleet-median batch | extra req/s to the gate | extra req/s to the KV cap | extra req/s to fleet median |
|---|---|---|---|---|---|---|---|---|
| r1 `s0_m2` | 10.5 | 4,004 | 576 | 211 | 140.5 | +27.8 | **+9.9** | +6.4 |
| r2 `s0_m2` | 10.0 | 4,191 | 566 | 202 | 147.5 | +28.7 | **+9.9** | +7.1 |
| r1 `s1_A` | 144.0 | 5,752 | 490 | 147 | 144.8 | +5.1 | **+0.04** | +0.01 |
| r2 `s1_A` | 148.5 | 5,767 | 490 | 147 | 144.5 | +4.3 | **0.00** | 0.00 |
| r1 `s2_m1` | 136.0 | 5,626 | 496 | 150 | 162.5 | +4.9 | **+0.19** | +0.36 |
| r2 `s2_m1` | 127.0 | 5,974 | 481 | 141 | 180.2 | +4.5 | **+0.18** | +0.68 |
| r1 `s3_B` | 145.2 | 5,872 | 485 | 144 | 192.5 | +4.2 | **0.00** | +0.59 |
| r2 `s3_B` | 145.5 | 5,857 | 486 | 144 | 195.5 | +4.2 | **0.00** | +0.62 |

Two readings, and they point in different directions.

**In `s1_A`, `s2_m1` and `s3_B` the answer is that nothing is available.** The gate would
allow 4.2 to 5.1 more requests per second on the freed engine, and 0.00 to 0.19 of that is
reachable, because the engine is already within 4 % of the KV cap. The "extra to fleet median"
column is larger than the "extra to KV cap" column in `s2_m1` and `s3_B`, which means the
fleet-median load is itself infeasible on that engine under A5. In these three segments the
fleet turns away 11.2 % to 31.7 % of arrivals, so demand for the headroom exists; the headroom
does not.

**In `s0_m2` the capacity is real and the demand is not.** Loading the freed engine to the
fleet-median batch of 140.5 and 147.5 would add 6.4 and 7.1 requests per second, would put it
at 34.3 and 35.6 ms of modelled step against a 90.0 ms gate, and would raise its output from 528 and
514 tokens per second to a modelled 4,095 and 4,148. But the only requests it can take without
losing the chat-free property are deep research and swe, and those arrive at 1.14 and 0.57
requests per second in that segment, against 22.72 for chat. The fleet rejects 1.6 % and 2.0 %
of arrivals there. **Freeing one engine of four from the majority class, when that class is
93 % of arrivals, necessarily leaves that engine holding at most 7 % of the offered request
stream.** The preference is self-limiting in exactly the regime where `a1_separation.md`
records its largest structural effect.

## 6. Question 5 — is the fleet's work more evenly spread with the preference off?

**Definition, stated exactly.** For each 60 s window, take the four engines' values of a work
quantity, form the shares `s_i` that sum to one, and compute `N_eff = 1 / sum(s_i^2)`. This is
the inverse Simpson index, equivalently the numbers equivalent of the Herfindahl index and
equal to four times Jain's fairness index over four engines. Its unit is engines: 4.00 means
the four engines carry identical work, 1.00 means one engine carries all of it, and 2.00 means
two engines split it evenly. The statistic is computed per window and reported as the median
over the 59 windows of a run, so no ratio of ratios is averaged. The coefficient of variation
of the same four values is reported beside it.

| arm, repeat | `N_eff` on decode batch | `N_eff` on output tokens/s | `N_eff` on logical KV | CV of decode batch | CV of output tokens/s |
|---|---|---|---|---|---|
| ON r1 | 3.837 | 3.604 | 3.565 | 0.206 | 0.331 |
| ON r2 | 3.830 | 3.587 | 3.548 | 0.211 | 0.340 |
| OFF r1 | 3.968 | 3.883 | 3.884 | 0.089 | 0.174 |
| OFF r2 | 3.971 | 3.903 | 3.961 | 0.086 | 0.157 |
| llm-d r1 | not available | 3.977 | not available | not available | 0.077 |
| llm-d r2 | not available | 3.956 | not available | not available | 0.105 |

**The preference-off fleet is more evenly loaded, and the difference is resolved.** On decode
batch the gap is 0.136 engines against repeat spreads of 0.007 and 0.003. On output tokens per
second it is 0.298 engines against spreads of 0.017 and 0.020. The coefficient of variation of
decode batch more than doubles, from 0.086 and 0.089 to 0.206 and 0.211. llm-d, which is here
for context and whose per-instance scheduler gauges are all zero, is the most even of the
three at 3.977 and 3.956 on output tokens per second.

Per segment, the unevenness is concentrated where the freed engine is empty:

| segment | ON r1 | ON r2 | OFF r1 | OFF r2 |
|---|---|---|---|---|
| `s0_m2` | 2.514 | 2.559 | 3.976 | 3.971 |
| `s1_A` | 3.964 | 3.962 | 3.975 | 3.980 |
| `s2_m1` | 3.641 | 3.508 | 3.960 | 3.977 |
| `s3_B` | 3.872 | 3.900 | 3.945 | 3.952 |

(`N_eff` on decode batch, median over the 15 windows of each segment.) In `s0_m2` the
preference concentrates the fleet's decoding work onto the equivalent of 2.51 and 2.56 engines
out of four, while the preference-off arm keeps it on 3.97 and 3.98. In `s1_A` the two arms differ by 0.015 engines out of
four, which is the smallest per-segment gap in the table.

**Uneven does not mean less total work here.** Fleet totals, summed across the four engines
per window and reduced to the median over the 59 windows:

| quantity | ON r1 | ON r2 | OFF r1 | OFF r2 | difference | repeat spread |
|---|---|---|---|---|---|---|
| output tokens per second | 11,831 | 11,975 | 11,765 | 11,738 | +152 | 144 (ON), 27 (OFF) |
| decode batch | 616.5 | 613.0 | 584.0 | 578.5 | +33.5 | 3.5 (ON), 5.5 (OFF) |
| logical KV tokens | 1,853,065 | 1,904,967 | 1,567,292 | 1,573,021 | +309,000 | 51,900 (ON), 5,700 (OFF) |
| requests routed per second | 21.8 | 21.6 | 21.9 | 21.8 | −0.15 | 0.2 (ON), 0.1 (OFF) |

**The fleet output rate does not move: the +152 tokens per second is inside the ON arm's own
144-token repeat spread and must be reported as no difference.** What does move is what the
fleet holds. The preference-on fleet keeps 5.7 % more requests decoding and 19.7 % more logical
KV tokens resident, and it converts them into the same number of output tokens per second at
the same routed request rate. Grouping deep research onto one engine raises the fleet's
resident memory without raising its production.

## 7. What this means for the design

The five-dimension summary proposed one design change if the answer came back "underused":
make the sort send more work to the loosened engine. **That change addresses one of the two
regimes and would not help in the other.**

In the 93 %-chat segment it is the right diagnosis and the wrong remedy. The freed engine is
genuinely empty, at 0.217 and 0.219 of its gate and 7 % of a peer's decode batch, and the sort
does not send it more work. But the work that could be sent without re-mixing chat onto it
does not exist: deep research and swe together arrive at 1.71 requests per second against a
freed capacity of 6.4 to 7.1 requests per second under the arithmetic of section 5. **The
mechanism that would help there is not a sort change but a bound on how much of the fleet the
preference may reserve, tied to the minority classes' share of arrivals.** One engine of four
is 25 % of the fleet; the minority classes are 7 % of arrivals in that segment.

In the other three segments the loosened engine is at the physical KV ceiling for 42 % to 83 %
of its seconds and preempts 21 to 40 times per minute. Sending it more work is not available:
0.00 to 0.19 additional requests per second under A1 to A6, against 4.2 to 5.1 that the pace
gate alone would allow. **The binding constraint on the freed engine is memory, and it becomes
memory precisely because the class the preference groups there carries 5,667 and 5,541 logical
KV tokens per decode slot against 2,422 and 2,203 for a chat-carrying engine.** This is the
outcome-layer counterpart of the placement-layer trade `README.md` section 3 measures, where
refusals move from the pace test to the KV test by 6.1 points in each direction. Section 3
inferred that trade from the equality of the two movements. This appendix measures the same
thing on the engine the preference frees: its pace test has 58 points of slack by the model
and its memory test has none.

A third consequence concerns the capacity model rather than the policy. The measured
inter-token latency of a freed engine is 1.781 and 1.790 times its modelled decode step,
against 1.326 and 1.314 on a chat-bound engine. **The policy therefore over-estimates the
freed engine's headroom by the largest margin exactly on the instance whose headroom its
structural argument depends on.** Adding the prefill and preemption terms the decode-only law
omits would reduce the modelled headroom on the freed engine from 58 % of the gate to about
22 %, which would change which instances the feasibility test admits.

## 8. What this appendix did not verify

1. **No preference-off counterfactual of the freed engine's load.** Section 5 asks what the
   freed engine could have taken; it does not compare against what the same requests received
   in the preference-off arm, because the engine identity is not comparable between arms.
2. **The step model is used outside the range it was fitted on.** Section 5 evaluates it at
   batches of 141 to 576 with 4,000 to 6,000 KV tokens per slot. The fit reports 76 cells and
   a p90 relative error of 20.5 % without stating the batch range it covers, and no check was
   made that batches above 200 are inside it.
3. **The KV cap of 845,000 logical tokens is inferred from the runs, not read from
   configuration.** It is the median logical KV over windows whose physical gauge reads at
   least 0.995. Prefix sharing makes it workload-dependent, so it is not a machine constant,
   and section 5's "extra to KV cap" column inherits that dependence.
4. **Little's law is applied with a residency time taken from the same steady state it is
   used to perturb.** `D` from `n_observed / routed_per_second` is 68.6 to 81.2 s on the
   saturated engines, and part of that is queueing that would grow if more work arrived.
5. **Requests routed per second is timestamped by client arrival, not by scheduler dispatch.**
   `analysis/request_engine.csv` carries no dispatch timestamp, so a request held by the
   gateway is counted in the window it arrived in rather than the window it was placed in.
6. **Output length is a lower bound for requests cut off at the run boundary.** The
   output-token rate in section 3 comes from the engine's `vllm:generation_tokens_total`
   counter and is not affected, but the residency intervals that classify the windows use the
   client's latency, which for a cut-off request ends at the cut.
7. **No per-class engine counters exist.** The claim that the freed engine's high KV per
   decode slot comes from deep research rests on the class composition of the requests routed
   there, not on an engine-side per-class measurement.
8. **The preemption counter was differenced across windows and not attributed to requests.**
   Whether the 21 to 40 preemptions per minute fall on deep research or on the swe requests
   sharing the freed engine is not established.
9. **Two repeats per arm, and the two arms ran in different session blocks.** Every spread
   quoted here is the range of two values. This repository has measured 26 % session-to-session
   movement in preemption count for an identical configuration, so the preemption asymmetry
   between chat-free and chat-carrying windows is safe (0 against 28 to 40 per minute, within
   one run) while any preemption comparison between arms is not.
10. **The llm-d runs contribute one column.** Their scheduler gauges are all zero, so the only
    llm-d quantity here is the spread of output tokens per second across engines. No residency,
    no pace and no admission decision was read for that baseline.
11. **The 51.9 ms gate case was not analysed separately.** Chat-free windows in which swe is
    resident have a gate of 51.9 ms rather than 90.0 ms, and they are 11 of the 55 and 12 of
    the 58 chat-free windows. They are pooled into the section 4 distributions.
12. **Mixed windows were reported and then dropped.** The 12, 15, 6 and 5 windows in which
    chat is resident for part of the window carry no result here.
