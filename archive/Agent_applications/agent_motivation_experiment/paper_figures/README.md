# paper_figures

---

# 채점 규칙 — 2026-09-03에 고정했다. 이 디렉토리의 모든 그림이 이것을 쓴다

**규칙을 가진 스크립트는 하나다**: `analysis_scripts/request_level/deadline_ladder_attainment.py`.
그림은 그 스크립트의 표(run 단위)나 요청 단위 판정(`--dump-verdicts`)을 **읽기만 하고 다시
계산하지 않는다.**

```
토큰 i 가 제때:  보낸 시각 + TTFT_SLO + i × TBT_SLO 안에 도착
                 i 는 0부터 세므로 첫 토큰의 마감은 정확히 TTFT_SLO
요청이 제때:     그 요청 토큰의 95% 이상이 제때
token goodput:   자기 마감을 지킨 토큰의 개수 ÷ 창 (요청 단위가 아니라 토큰 단위)
```

클래스 예산: chat (5 s, 50 ms) · deepresearch (10 s, 100 ms) · **swe (7 s, 75 ms)**.
분모(offered / admitted / 미완 제외)와 warmup 60초·drain 20초는 `exp22_fluidserve.py`를 따른다.

**첫 토큰에 별도 조건이 없다.** 첫 토큰은 같은 사다리의 토큰 0일 뿐이라, 늦으면 관용 5% 안에서
용서될 수 있다. ⚠ **그러므로 출력이 긴 클래스에서 TTFT 예산은 약하게만 구속한다** — 뒤 토큰이
예산보다 빠르면 첫 토큰의 지각이 사다리에서 흡수된다. 이것은 알고 고른 것이고, 캡션이 첫 토큰
성능을 주장할 때는 이 규칙이 아니라 TTFT 분포를 따로 인용해야 한다.

**산출물**: `results/aggregate_analysis/ladder95/` — `exp108_ladder95.csv`(EXP-108 64 run),
`exp108_paper_ladder95.csv`(논문이 그리는 run 집합, `build_paper_ladder_table.py`가 만든다),
`vllm77_ladder95.csv`, `exp110_ladder95.csv`, `exp109_hour_ladder95.csv`, 그리고
`verdicts/`(run마다 요청 단위 판정 CSV).

⚠ **이 규칙 이전에 그려진 수치를 이 규칙의 수치와 같은 표에 넣지 않는다.** 바뀐 것이 셋이다 —
관용이 90%에서 **95%**로, goodput이 요청 단위에서 **토큰 단위**로, 첫 토큰 별도 조건이 **없어졌다.**

---

Figures built for the paper. Each is drawn at its final physical size so that
LaTeX includes it at a scale factor of 1.0 and the type lands on the page at the
size it was set at. **Do not re-scale these in LaTeX** — `scale=`, `\resizebox`,
or a `width=` other than the one named below multiplies every font size by the
same factor.

| Script | Output | Size | Include as |
|---|---|---|---|
| `fig_exp27_pass3.py` | `exp27_pass3_attainment_goodput_offered.pdf` | 3.335 × 2.22 in | `figure`, `width=\columnwidth` |
| | `exp27_pass3_attainment_goodput_admitted.pdf` | 3.335 × 1.95 in | `figure`, `width=\columnwidth` |
| `fig_exp53_policies.py` | `exp53_attainment_goodput_offered.pdf` | 3.335 × 2.22 in | `figure`, `width=\columnwidth` |
| | `exp53_attainment_goodput_admitted.pdf` | 3.335 × 1.95 in | `figure`, `width=\columnwidth` |
| `fig_exp50_hour.py` | `exp50_hour_attainment_goodput.pdf` | 7.000 × 1.60 in | **`figure*`**, `width=\textwidth` |
| `fig_workload_lengths.py` | `workload_lengths.pdf` | 3.335 × 1.95 in | `figure`, `width=\columnwidth` |
| `fig_azure_trace_shape.py` | `azure_trace_shape.pdf` | 3.335 × 1.60 in | `figure`, `width=\columnwidth` |
| `fig_exp54_hour.py` | `exp54_hour_offered.pdf` | 7.000 × 1.60 in | **`figure*`**, `width=\textwidth` |
| | `exp54_hour_admitted.pdf` | 7.000 × 1.60 in | **`figure*`**, `width=\textwidth` |

`paper_style.py` holds the width, the rcParams, the arm colours and markers, the
`k`-suffix tick formatter, and `save()`. Import from it rather than copying the
constants; `save()` in particular exists so that no figure here reintroduces
`bbox_inches="tight"`.

3.335 in is the USENIX single column: `usenix2019_v3.sty` sets `\textwidth=7in`
and `\columnsep=0.33in`. All type is 8 pt except the panel-internal annotations.
Fonts are embedded as Type 42.

---

## `fig_exp27_pass3.py` — attainment and token goodput vs offered rate

Two side-by-side panels: (left) SLO attainment, (right) output-token goodput,
both against offered request rate. Two versions, differing only in whether the
offered-denominator curves are drawn.

### Runs

Twenty run directories, all `m1` balanced mix, four engines, 8 minutes per
condition, `--restart-per-condition` so each condition is independent.

| Arm | Legend | Colour / marker | Runs | n per rate | Session |
|---|---|---|---|---|---|
| `polyserve` | PolyServe | `#d62728` ○ | `results/260728_00*_exp27p3r{1,2}_polyserve_m1_rpm_*` | 2 | 2026-07-28 00:08–02:45 |
| `slo` | Llumnix | `#2ca02c` △ | `results/260728_0[89]*_exp27p5r1_slo_m1f_rpm_*` | **1** | 2026-07-28 08:38–09:21 |
| `fluidserve` | FluidServe | `#1f77b4` □ | `results/260728_01*_exp27p3r{1,2}_fluidserve_m1_rpm_*` | 2 | 2026-07-28 01:06–03:37 |

Rates: 1200 / 2400 / 3600 / 4800 rpm = **20 / 40 / 60 / 80 req/s**.

The glob is `RUNS` in the script: `results/*exp27p3*` and `results/*exp27p5*`.

**Pass 4 is deliberately excluded.** `exp27p4*` is the same Llumnix baseline at
the 25 ms/token setting, which judged the agent class 2.3× tighter than
FluidServe judged it and rejected 98% of it; that curve belongs to a policy
answering a different question. This matches the exclusion in
`analysis_scripts/request_level/exp27_figures.py`, which is the authority on the
run selection.

The baseline runs are tagged `m1f`, not `m1`. `mix_of` folds the `f` variant
onto `m1` — same three workloads, same ratio, same arrival process and seed;
only the `(ttft_ms, tbt_ms)` pair the baseline is configured with differs.

### Metrics

Computed by `analysis_scripts/request_level/exp22_fluidserve.py`, which the
figure script imports rather than reimplements, via `exp27_figures.collect`.

- **Analysis window** — arrivals from 60 s after the first request to 20 s
  before the last completion. Requests still in flight at the end are excluded
  from both denominators; their outcome was never determined.
- **Violation** — per class: chat TTFT > 5 s or ITL > 50 ms; deep research
  TTFT > 10 s or ITL > 100 ms; agent (swe) end-to-end > 30 s. A request that
  produced no first token is a violation under any rule.
- **SLO attainment, admitted** (solid) — denominator is the requests the system
  accepted. A rejected request leaves the population.
- **SLO attainment, offered** (dotted) — denominator is every request that
  arrived; a rejection counts as a violation.
- **Goodput token (t/s)** — output tokens from requests that met their rule on
  the offered denominator, divided by the window. Whole-request: a request that
  missed contributes none of its tokens. Input tokens never count.
- Aggregation is **per request**, every request counting once. Not the
  class-equal average.

**Inter-token latency is the corrected quantity.** It is derived as
`(e2e − ttft) / (output_tokens − 1)`, not read from the recorded `tbt_mean_ms`
column, which is about half the true value on every run collected before
2026-07-30 (`fluidserve-implementation.md` §32). These runs predate that, so
they score very differently under the two bases:

| | uncorrected | corrected (drawn) |
|---|---|---|
| FluidServe @ 80 req/s, admitted | 87.0% | **48.1%** |
| Llumnix @ 60 req/s, admitted | 93.7% | **28.6%** |

Any table or figure of these runs quoted from before 2026-07-30 is on the
uncorrected basis. `FS_LEGACY_TBT=1` reproduces it.

The defect is in what the client recorded, so it is bounded by run date, not by
analysis date. Ratio of the recorded column to the corrected value, chat median,
one run per experiment:

| Runs | recorded / corrected |
|---|---|
| EXP-27 pass 3 and pass 5 (2026-07-28) | **0.52** — affected |
| EXP-41 (2026-07-30 20:13 on) | 1.04 |
| EXP-42 (2026-07-31) | 1.01 |

So only this figure's runs are moved by the correction. EXP-41 and EXP-42 were
collected after the client was fixed and read the same either way.

### Per-class attainment, admitted denominator

The aggregate is per request, and chat is 76.9% of the requests, so the
aggregate tracks chat. Reporting it alone would hide that the three classes fail
at completely different rates.

| Arm | req/s | chat | deep research | agent (swe) |
|---|---|---|---|---|
| PolyServe | 20 | 100.0 | 100.0 | 99.7 |
| PolyServe | 40 | **16.6** | 100.0 | 100.0 |
| PolyServe | 60 | **6.2** | 100.0 | 99.5 |
| PolyServe | 80 | **3.0** | 99.9 | 98.6 |
| Llumnix | 20 | 100.0 | 100.0 | 100.0 |
| Llumnix | 40 | 99.8 | 100.0 | 100.0 |
| Llumnix | 60 | **10.8** | 100.0 | 52.0 |
| Llumnix | 80 | **5.2** | 100.0 | 40.0 |
| FluidServe | 20 | 100.0 | 100.0 | 100.0 |
| FluidServe | 40 | 99.9 | 100.0 | 100.0 |
| FluidServe | 60 | 96.5 | 99.9 | 91.9 |
| FluidServe | 80 | 38.2 | 94.5 | 65.3 |

Every collapse in this figure is the chat class missing its 50 ms inter-token
budget. Deep research, whose budget is 100 ms, is at or near 100% for all three
arms at every rate; the agent class, judged end to end, only moves for Llumnix
and for FluidServe at 80 req/s. This is the reason the correction matters so
much here: it is the per-token half of the rule that was being judged against
twice its budget, and chat is the class with the tightest per-token budget and
the largest share of the requests.

### Values drawn

Mean over repeats; range is min..max.

| Arm | req/s | n | Attain. admitted | range | Attain. offered | range | Goodput t/s | range | Rejected |
|---|---|---|---|---|---|---|---|---|---|
| PolyServe | 20 | 2 | 100.0 | 99.9–100.0 | 100.0 | 99.9–100.0 | 8,528 | 8,510–8,546 | 0.0% |
| PolyServe | 40 | 2 | 39.3 | 38.6–40.0 | 39.3 | 38.6–40.0 | 5,004 | 4,908–5,101 | 0.0% |
| PolyServe | 60 | 2 | 32.5 | 32.0–33.0 | 32.5 | 32.0–33.0 | 5,836 | 5,739–5,932 | 0.0% |
| PolyServe | 80 | 2 | 28.4 | 28.2–28.6 | 28.4 | 28.2–28.6 | 7,258 | 7,208–7,309 | 0.0% |
| Llumnix | 20 | 1 | 100.0 | — | 100.0 | — | 8,619 | — | 0.0% |
| Llumnix | 40 | 1 | 99.9 | — | 99.9 | — | 16,525 | — | 0.0% |
| Llumnix | 60 | 1 | 28.6 | — | 27.1 | — | 4,763 | — | 5.3% |
| Llumnix | 80 | 1 | 32.5 | — | 20.6 | — | 4,674 | — | 36.1% |
| FluidServe | 20 | 2 | 100.0 | 99.9–100.0 | 100.0 | 99.9–100.0 | 8,573 | 8,562–8,584 | 0.0% |
| FluidServe | 40 | 2 | 99.9 | 99.9–99.9 | 99.9 | 99.8–99.9 | 16,546 | 16,521–16,571 | 0.1% |
| FluidServe | 60 | 2 | 96.8 | 96.7–97.0 | 87.8 | 87.2–88.3 | 21,371 | 21,345–21,398 | 9.3% |
| FluidServe | 80 | 2 | 48.1 | **40.1–56.2** | 33.6 | 28.1–39.1 | 9,861 | 7,719–12,002 | 29.6% |

### What the caption has to carry

The drawing cannot say these, and two of them change how the figure reads.

1. **The rejection rates.** Attainment on an admitted denominator is not
   interpretable without them — a policy that refuses everything scores 100%.
   Llumnix rejects 5.3% at 60 req/s and 36.1% at 80; FluidServe 9.3% and 29.6%;
   PolyServe never rejects. In the `_admitted` version this is the only place
   the reader can learn it.
2. **The baseline is one run per rate**, so it has no bars. A point with no bar
   beside points with bars reads as the more precise measurement, which is the
   opposite of the truth.
3. **The baseline ran in a separate session** (08:38–09:21) from the other two
   arms (00:08–03:37). The size of session-to-session movement on this workload
   was measured as 0.1 attainment points at 20 req/s and 4.6 at 80, but that was
   measured on the uncorrected basis and has not been recomputed.
4. **The n=2 spread is not uniformly small.** Under 0.5 points at 20–60 req/s on
   every arm, but **16.1 points on FluidServe at 80 req/s** (40.1 vs 56.2). No
   claim should rest on that point.

### Regenerating

```bash
cd agent_motivation_experiment
python3 paper_figures/fig_exp27_pass3.py
```

Both PDFs are rewritten. The script reads the run directories directly; it holds
no cached numbers, so a change in `exp22_fluidserve.load_run` changes the figure
silently. Re-run it after any change to the loader and compare against the table
above.

---

## Why EXP-42's FluidServe cannot be substituted into this figure

Asked 2026-08-01. It cannot, for two independent reasons.

**The rates do not line up.** EXP-42 measured 900 / 1800 / 2700 / 3600 rpm =
15 / 30 / 45 / 60 req/s. This figure is at 20 / 40 / 60 / 80. Only 60 req/s is
shared, so three of four x positions would have no EXP-42 measurement and the
other two arms would have none at 15 / 30 / 45.

**The workload changed.** Same workload name (`mixed_request_level_poisson`),
same mix weights (chat 10 : deep research 2 : swe 1), same transcript file, and
the same SLO block — but the deep-research class was reworked between them. Per
class, over completed requests only (so rejections do not distort it):

| Class | EXP-27 pass 3, output tokens mean / p50 | EXP-42, mean / p50 |
|---|---|---|
| chat | 423 / 389 | 415 / 381 — unchanged |
| agent (swe) | 490 / 487 | 487 / 487 — unchanged |
| **deep research** | **284 / 250** | **990 / 976 — 3.5×** |

EXP-42's `run_config.json` records the cause: "deep research moved 4,055 → 4,639
when its system prompt grew and its notes became the question's own". Measured
input tokens agree (4,122 → 4,363 for deep research; chat and swe unchanged).
`max_tokens` also went from unset to 4096, but EXP-27's deep-research p95 output
was 535 tokens, so nothing was near a cap and the cap is not the cause.

Weighting by arrival shares (chat 76.9%, deep research 15.4%, swe 7.7%), the
mean decode tokens per request goes from 406 to 509, so **the same nominal
request rate is about 25% more decode work in EXP-42**. That is why FluidServe
at the one shared rate reads 96.8% admitted here and 56.2% in EXP-42, and it is
consistent with EXP-42 having moved its sweep down to 15–60 req/s.

Putting a series from each into one figure would place three arms on three
different workloads. An EXP-42 figure has to be built from EXP-42 arms only.

---

## `fig_exp53_policies.py` — four control planes on one static rate sweep

Same two panels and same geometry as the EXP-27 figure, four arms instead of
three.

### Runs

Thirty-two conditions, `results/*exp53r*`, m1 balanced mix, 8 minutes each,
`--restart-per-condition`. Rates 900–4200 rpm = **15, 25, 35, 45, 50, 55, 60,
70 req/s**. Run selection and the per-condition aggregation come from
`analysis_scripts/request_level/exp53_compare.py`, which this script imports.

| Arm | Legend | Colour / marker |
|---|---|---|
| `fluidserve` | FluidServe | `#1f77b4` □ |
| `polyserve` | PolyServe | `#d62728` ○ |
| `slo` | Llumnix SLO | `#2ca02c` △ |
| `loadbalance` | Llumnix | `#9467bd` ▽ |

**Every cell is n=1.** All 32 conditions are single runs, so no point on this
figure carries an error bar and none of the spread numbers that exist for
EXP-27 apply here. This is the single most important thing the caption has to
say, because the reader's default reading of an unbarred point is that it is
precise.

### Values drawn

`off` = offered denominator, `adm` = admitted, `gp` = goodput in output tokens/s.

| req/s | FluidServe off / adm / rej / gp | PolyServe off=adm / gp | Llumnix SLO off / adm / rej / gp | Llumnix off=adm / gp |
|---|---|---|---|---|
| 15 | 100.0 / 100.0 / 0% / 8,049 | 100.0 / 7,883 | 100.0 / 100.0 / 0% / 7,846 | 100.0 / 8,023 |
| 25 | 100.0 / 100.0 / 0% / 13,419 | 99.7 / 13,214 | 99.9 / 99.9 / 0% / 13,387 | 100.0 / 13,357 |
| 35 | 100.0 / 100.0 / 0% / 17,879 | 40.5 / 8,379 | 99.9 / 99.9 / 0% / 18,163 | 99.7 / 18,093 |
| 45 | 90.0 / 97.6 / 7.6% / 20,426 | 27.6 / 5,519 | 51.9 / 56.7 / 8.4% / 12,390 | 32.2 / 9,098 |
| 50 | 77.9 / 93.0 / 16.1% / 20,072 | 26.8 / 5,571 | 34.7 / 44.0 / 20.7% / 10,760 | 14.3 / 3,727 |
| 55 | 66.3 / 88.4 / 24.3% / 19,019 | 18.1 / 4,123 | 30.7 / 48.6 / 36.1% / 11,072 | 9.8 / 2,438 |
| 60 | 66.7 / 97.7 / 31.0% / 20,775 | 17.0 / 4,122 | 26.4 / 54.0 / 50.3% / 11,099 | 7.3 / 1,749 |
| 70 | 51.5 / 94.0 / 44.3% / 19,454 | 13.7 / 3,937 | 21.1 / 69.8 / 68.9% / 11,659 | 4.0 / 842 |

### Why the offered version is the one to use

This sweep is the clearest case in the project of the admitted denominator
misleading. **Llumnix SLO's admitted attainment RISES from 44.0% at 50 req/s to
69.8% at 70 req/s while its rejection rate goes 20.7% → 68.9%.** It is not
recovering; it is refusing more than two thirds of arrivals and scoring itself
on the third it kept. On the offered denominator the same arm falls 34.7 → 21.1.
FluidServe's admitted curve has the same shape for the same reason, though less
extremely (31.0% → 44.3% rejected).

The admitted-only PDF is for the case where the surrounding text already gives
the rejection rates. If it does not, use the offered one.

### Open discrepancy — migration on PolyServe

`exp53_compare.py` prints a standing note on every figure: *"Llumnix arms run
with migration enabled; FluidServe and PolyServe do not use it and run
without."* Its own counter, reading `server_metrics/migration_events.log`,
disagrees for PolyServe:

| Arm | non-zero rescheduling pairs, summed over its conditions |
|---|---|
| FluidServe | 0 |
| **PolyServe** | **225** (8 at 45 req/s, 27 at 50, 40 at 55, 60 at 60, 90 at 70) |
| Llumnix SLO | 0 |
| Llumnix | 13 |

So the arm the note says ran without migration is the one with by far the most
rescheduling pairs, and one of the two arms the note says had it enabled
produced none. **This is unresolved and the note is not reproduced on the paper
figure until it is.** Either the note is wrong about how PolyServe was
configured, or `rescheduling_pairs` counts something other than what the note
means by migration. Whichever it is, the asymmetry between the arms is not
currently established in the direction the note claims.

### Regenerating

```bash
cd agent_motivation_experiment
python3 paper_figures/fig_exp53_policies.py
```

---

## `fig_exp50_hour.py` — the hour, FluidServe against Llumnix SLO

Two side-by-side panels against time: (left) SLO attainment on the **admitted**
denominator, (right) output token goodput. **This is the only figure here drawn
at the full text width (7.0 in), so it goes in a `figure*` at
`width=\textwidth`** — an hour at 30 s steps is ~119 points per line, which is
unreadable in a 1.3 in panel.

It is panels D and G of `results/aggregate_analysis/exp50/hour_timeline.png`,
reduced from four arms to the two being compared and redrawn at paper size.

### Runs

One hour of moving load, the same trace for both arms, **one run each, from
different experiments a day apart**.

| Arm | Legend | Colour / style | Run |
|---|---|---|---|
| FluidServe, corrected length profile | FluidServe | `#1f77b4` solid | `results/260801_1808_exp50p2r1_fluidserve_full` |
| Llumnix SLO | Llumnix SLO | `#2ca02c` dashed | `results/260731_2203_exp45r1_slo_full` |

The trace steps its class mix every 15 minutes — **m1, m2, m3, m1, fifteen
minutes each** — marked with grey vertical guides at 15, 30 and 45 min. The
segment tags are no longer drawn on the figure, so **the caption has to name
them**, otherwise the three guides are unexplained lines. Both curves move at
those instants, which is what makes the shape attributable to the workload
rather than to the policy. That the two arms saw the same trace is verified by
panel A of the source figure, which draws the offered rate per arm and gets one
curve.

### Metrics

Imported from `exp41_dynamic_timeline.py` and `exp22_fluidserve.py`, not
reimplemented.

- **Window** — 90 s wide, stepped every 30 s, anchored on **arrival**. A point
  at minute *t* is "of the requests that arrived around *t*, this is what
  happened to them"; a request arriving at *t* and finishing at *t*+30 s is
  scored at *t*. Windows holding fewer than 30 requests are dropped.
- **SLO attainment, admitted** — rejections leave the denominator entirely. This
  is the quality of the work the policy chose to do, not the fraction of offered
  load that was served.
- **Goodput token (t/s)** — output tokens/s from requests that met their rule,
  judged whole-request. A rejected request produced no tokens and contributes
  none; that is a fact about tokens, not a choice of denominator, so this panel
  is the same under either.

### Whole-hour figures

| Arm | Attainment, admitted | Attainment, offered | Rejected |
|---|---|---|---|
| FluidServe | **95.5%** | 69.7% | 27.0% |
| Llumnix SLO | 66.3% | 38.3% | 42.1% |

### What the caption has to carry

1. **The rejection rates above.** The admitted denominator is the flattering one
   for both arms and neither rejection rate appears anywhere on this figure.
   FluidServe's 95.5% admitted is 69.7% offered; Llumnix SLO's 66.3% is 38.3%.
   Without those numbers the left panel overstates both arms, and it overstates
   the baseline more, because the baseline rejects more.
2. **One run per arm.** No point on either line is an average and no wiggle is
   noise-bounded. Every feature is one realisation.
3. **The two runs are from different experiments a day apart** — EXP-45 for the
   baseline, EXP-50 part 2 for FluidServe. Same trace, different session.
4. **What the three vertical guides mark**: the class mix steps m1 → m2 → m3 →
   m1 at 15, 30 and 45 minutes. Nothing on the figure says so any more.

### Regenerating

```bash
cd agent_motivation_experiment
python3 paper_figures/fig_exp50_hour.py
```

It prints the window count and the whole-hour figures for each arm; check them
against the table above.

---

## The workload, for the paper's setup section

Three request classes drawn from three sources, arriving as one request-level
Poisson stream with classes drawn in shuffled fixed-composition blocks. Model:
`meta-llama/Meta-Llama-3.1-70B-Instruct`, four engines, `max_tokens` 4096.

| Class | Source | Dataset |
|---|---|---|
| chat | `sharegpt_request_level_poisson` | ShareGPT — HF `anon8231489123/ShareGPT_Vicuna_unfiltered`, `ShareGPT_V3_unfiltered_cleaned_split.json`, rev `192ab218`, 1,000 conversations → 3,354 requests, seed 42. Multi-turn chat turns sent as independent requests |
| deep research | `searcharena_request_level_poisson` | Search Arena — HF `lmarena-ai/search-arena-24k`, `data/search-arena-chat-24k.parquet`, rev `fac8dcf8`, English, 12,603 questions and 33,152 notes → 60,000 requests, K notes per request with K ∈ [2, 12], seed 42. Each request is a synthesis over search-grounded notes |
| agent (swe) | `codingagent_request_level_poisson` | SWE-bench Lite — literal replay of a recorded concurrency-1 transcript, `transcript_swe_short7k_mix1500.jsonl`. Prompts are replayed verbatim, so the input distribution is fixed by the recording |

Mix on `m1`: request counts chat 10 : deep research 2 : agent 1, i.e. **76.9 /
15.4 / 7.7 %** of requests.

### Length distributions

Tokens, measured at the gateway. Output is over **completed requests only** (not
rejected, errored, or cut off at run end), pooled over the four arms of EXP-53
at **15 req/s** — the lowest rate in that sweep, where all four arms hold 100%
attainment and reject nothing, so the lengths are set by the workload and not
truncated by saturation. The four arms agree to within 2% on every class median,
which is the check that this is a workload property rather than a policy one.

| Class | n | Input median | Input p90 | Input std | Output median | Output p90 | Output std |
|---|---|---|---|---|---|---|---|
| chat | 21,178 | 556 | 1,580 | 589 | 384 | 775 | 395 |
| deep research | 4,225 | 3,993 | 7,519 | 2,411 | 971 | 1,233 | 274 |
| agent (swe) | 2,157 | 5,725 | 7,787 | 1,416 | 517 | 648 | 119 |

Means, where they are wanted alongside: input 677 / 4,328 / 5,788, output 445 /
973 / 531.

The three classes are deliberately spread on both axes: chat is short in and
short out with a long tail (input std larger than its median), deep research is
long in and long out with the tightest output distribution, and the agent class
is the longest input with a short, tight output. That spread is what makes the
per-class SLO budgets bind at different rates.

### SLO rules

There are two SLO specifications and they are not the same object. Do not quote
one for the other.

**1. What attainment is scored against** — `SLO_RULES` in
`analysis_scripts/request_level/exp22_fluidserve.py`. A request is a violation
if it breaks its class rule, or if it produced no first token at all.

| Class | Rule |
|---|---|
| chat | TTFT ≤ 5 s **and** inter-token ≤ 50 ms |
| deep research | TTFT ≤ 10 s **and** inter-token ≤ 100 ms |
| agent (swe) | end-to-end ≤ 30 s |

These are **absolute thresholds**, not multiples of a solo baseline; `--tau` is
unused on these workloads. Inter-token latency is the corrected quantity,
`(e2e − ttft) / (output_tokens − 1)`.

**2. What is sent to the engine** — the `slo` block of the run config, packed
into the OpenAI `priority` field as `ttft_ms * 1000 + tbt_ms`, where the `tbt`
half doubles as PolyServe's tier key.

| Class | `ttft_ms` | `tbt_ms` | `out_len` |
|---|---|---|---|
| chat | 5,000 | 50 | 386 |
| deep research | 10,000 | 100 | 275 |
| agent (swe) | 11,800 | 25 | 728 |

The two agree for chat and deep research. For the agent class they are the same
budget written two ways: the class is scored end to end at 30 s, and the engine
needs a first-token deadline, so the 30 s is decomposed as
`ttft = 30,000 − out_len × tbt = 30,000 − 728 × 25 = 11,800 ms`. That is the
TTLT-to-first-token conversion in `workloads/swe_bench_coding/agent.py`.

Two stale numbers in that block, neither of which invalidates a measurement:

- **deep research `out_len` 275** against a measured median of 971. It is
  **inert**: `agent.py` only reads `out_len` on the e2e-only branch of the
  deadline calculation, and all three classes set `ttft_ms`, so that branch
  never runs.
- **agent `out_len` 728** against a measured median of 517. This one *was* used,
  once, to compute the 11,800 ms above. At 517 tokens the decomposition would
  give `30,000 − 517 × 25 = 17,075 ms`, so the engine is handed a first-token
  deadline about 5.3 s tighter than the 30 s end-to-end budget requires. That is
  conservative rather than wrong — the class is scored on the 30 s, which is
  unaffected — but it means the agent class is prioritised slightly harder than
  its own SLO demands.

### Note on output-length reproducibility

The same input does not always produce the same output length. Matched on
`(task_id, iteration)` over 9,660 requests, inputs were 100% identical while
outputs matched on only 65.7% (chat 72.4, deep research 44.2, agent 40.9%).
Continuous batching puts a request in a different batch each run, which changes
the floating-point reduction order and flips the choice at near-tied tokens.
Differences are usually small (|Δ| p50 = 0–4 tokens) but the tail is long
(p90 28–122, max 2,904). This is a property of batched serving, not a
misconfiguration, and it is one reason a single run per condition is not enough.

---

## `fig_workload_lengths.py` — length distributions of the three classes

`workload_lengths.pdf`, 3.335 × 1.95 in, `figure`, `width=\columnwidth`. Two
panels — (left) input tokens, (right) output tokens — each carrying one
probability density per class. This is the distribution behind the median / p90 /
std table above: the table gives three numbers per class, the density shows the
shape those numbers summarise.

Same population as the table — the four arms of EXP-53 at 15 req/s, pooled,
completed requests only, 27,560 requests.

**Reading it.**

- **chat** is the only broad distribution on either axis: input spans three
  decades with a secondary mode near 20 tokens (very short turns) and a main
  mode near 1k; output is a single wide mode near 400. Its input std (589)
  exceeding its median (556) is this shape.
- **deep research** is narrow on both axes and is the only class whose output
  mode sits above 1k tokens.
- **agent** has the highest input mode and a narrow output near 500. Its input
  is a small number of recurring prompt shapes — the transcript is replayed
  verbatim — which is why its curve is the spikiest.
- The two panels share one x range, so the horizontal distance between a class's
  input mode and its output mode is its expansion ratio and can be compared
  across classes by eye.

**How the density is computed.** Gaussian KDE over `log10(tokens)`, drawn
against a log x axis, because the data spans 2 to 15,334 tokens: a kernel wide
enough to smooth the high end erases the low end, and one narrow enough for the
low end leaves the high end a comb of spikes. Consequences:

- The curve is a density **per decade**, not per token. Area between two x values
  is the share of that class's requests in that range; height is not comparable
  to a linear-axis density. The y axis carries no numbers for that reason.
- **Each class integrates to 1 over its own requests**, not over the traffic. The
  curves answer "given a request of this class, how long is it". They do *not*
  show that chat is 77% of requests — the shares are in the legend.
- Heights are comparable between curves within a panel, not across the two
  panels.

---

## `fig_azure_trace_shape.py` — how much the source load varies

`azure_trace_shape.pdf`, 3.335 × 1.60 in, `figure`, `width=\columnwidth`.
Arrival rate over the four-day Azure window our dynamic trace is built from,
normalised to its own peak. The claim is the vertical extent.

### Why the SOURCE and not our replayed trace

Our trace is **not** a linearly rescaled Azure trace, and the difference decides
what this figure is allowed to say. `traces/dynamic/build_dynamic_mix_trace.py`
applies a **quantile (rank) transform** onto a chosen band — 10 to 50 req/s —
which keeps the temporal order and autocorrelation of the source (when Azure is
busy, we are busy) but replaces the distribution of rates with a uniform one
over the band. Its own docstring says to describe the result as "Azure-shaped,
rescaled to our cluster", not "an Azure trace".

So **the replayed trace's peak-to-trough ratio is 50/10 = 5.0× by
construction** — a parameter chosen so the run sweeps the band the fleet
resolves. Drawing that as evidence that real serving load varies would be
circular. The variation claim has to come from the source series, which is what
this figure draws.

A second consequence: time spent at each rate is uniform in the replay and is
not uniform in the source. Horizontal extents on this figure are not the
replay's.

### Window and numbers

`traces/azure/plots/_minute_{conv,code}2024.csv` — per-minute request counts of
the Azure LLM Inference 2024 conversation and code traces, summed index-wise —
restricted to `day_window=[0,4]` from `start_hour=6`, i.e. minutes 360–6120,
5,760 minutes, four days. The script reads those parameters from
`traces/dynamic/canonical/dyn60_azure4d.plan.json` rather than hardcoding them,
so regenerating the trace with a different window cannot leave this figure
describing the old one.

| | requests/min | fraction of peak |
|---|---|---|
| max | 9,049 | 1.000 |
| p95 | 7,351 | 0.812 |
| median | 3,887 | 0.430 |
| p5 | 2,181 | 0.241 |
| min | 1,568 | 0.173 |

**max / min = 5.77×**, **p95 / p5 = 3.37×**. Quote whichever matches the claim
being made and say which it is — the first is the extreme-to-extreme range and
is sensitive to single minutes, the second is the robust one. The generator's
docstring quotes ~3.6× for p95/p5, computed over a different window than the
four days used here.

The conv and code traces cover **different calendar weeks** and are summed by
index, which aligns hour-of-day phase but not calendar date. That is the
generator's documented caveat, carried here for completeness; it does not affect
the diurnal shape.

---

## `fig_exp54_hour.py` — three control planes on the hour-long dynamic trace

Two side-by-side panels against time: (left) SLO attainment, (right) output
token goodput. Full text width, `figure*`. Two versions differing only in the
attainment denominator. The dynamic counterpart of the EXP-53 static sweep, and
the four-policy counterpart of the EXP-50 hour figure.

### Runs

`dyn60_short_m123`, mean offered 50.1 req/s, about 179,000 requests. Arrival
rate follows an Azure production trace; the class mix steps m1 → m2 → m3 → m1 at
15-minute boundaries, marked with grey guides. **The guides are not labelled on
the figure, so the caption has to name them.** Repeat 1, 2026-08-03.

| Arm | Legend | Colour / style | Run |
|---|---|---|---|
| FluidServe | FluidServe | `#1f77b4` solid | `results/260803_1751_exp54r1_fluidserve_full` |
| PolyServe | PolyServe | `#d62728` dash-dot | `results/260803_1905_exp54r1_polyserve_full` |
| Llumnix SLO | Llumnix SLO | `#2ca02c` dashed | `results/260803_2117_exp54r1_slo_full` |

**One run per arm.** Repeat 2 was still running when this was drawn and covers
only FluidServe. Do not add that one arm's repeat by itself: an arm carrying a
band beside two arms without one reads as the better-measured arm rather than
the only repeated one.

### The load-balance arm is excluded, and not because it lost

`260803_2229_exp54r1_loadbalance_full` was stopped at 87% of its tasks and its
`metrics.csv` holds one row. It rejects nothing, so from minute 40 all four
engines saturated, long-queued requests had their streams cut, and the client
began failing to obtain source ports: **62,114 `[Errno 99] Cannot assign
requested address`, against zero in every other arm.** Its last twenty minutes
measure the load generator, not the policy. Drawing it as a fourth curve would
report a client defect as a policy result. State the exclusion and the reason.

### Which version to use

**The offered one, unless the surrounding text already gives the rejection
rates.** The three arms reject at completely different rates, so the admitted
denominator flatters them by completely different amounts:

| | rejected | offered | admitted | goodput tok/s | throughput tok/s |
|---|---|---|---|---|---|
| FluidServe | 27.0% | **70.0** | **95.9** | **18,014** | 18,944 |
| Llumnix SLO | 42.1% | 38.7 | 67.0 | 11,451 | 16,163 |
| PolyServe | 0.0% | 13.9 | 13.9 | 3,198 | 18,150 |

PolyServe's two attainment numbers are identical because it never rejects; the
other two gain 26 and 28 points from having refused work. The headline of this
figure is the last two columns together: **PolyServe produces 18,150 output
tokens/s against FluidServe's 18,944, a 4% difference, and 3,198 of them versus
18,014 land inside a latency rule.** Same fleet, same token production, 5.6× the
useful output.

### One value disagrees with the experiment write-up

`experiments/EXP-54_four-policy-hour.md` §2 gives PolyServe **17.6** on both
denominators. The runs give **13.9**, and that write-up's own per-class table
implies 13.9: 3.7 / 8.8 / 95.3 on chat / deep research / agent over 140,304 /
21,161 / 17,978 requests weights out to 13.9, not 17.6. Every other cell of that
table reproduces here exactly, including PolyServe's goodput (3,198) and
throughput (18,150), so this is one value in that document rather than a
difference of window or scoring. **Resolve it there before quoting either
number.** The figures here use 13.9.

### The x axis stops at 57.8 minutes, not 60

A request still in flight when the run ends has an unknown outcome, so it leaves
both denominators instead of counting as a violation. At the end of a backlogged
run that removes precisely the slow requests. **PolyServe's final window holds
3,678 arrivals of which 3,396 — 92.3% — never finished**; the 282 that did were
the fast ones, and attainment reads 99.6% against 9.1% two minutes earlier.
Drawn untrimmed, the figure shows the static partition recovering at the end,
which is the opposite of what happened.

Windows above 20% in-flight-at-end are dropped and **all arms are cut at the
same time**, which lands at 57.8 min. Maximum in-flight-at-end share per arm:
FluidServe 4.5%, Llumnix SLO 5.8%, PolyServe 92.3% — the artifact exists only on
the arm with a backlog, and it flatters that arm. The script prints the trim and
the per-arm maxima when it runs.

The EXP-50 hour figure was checked against the same threshold and needs no trim:
its two arms peak at 4.4% and 6.3%.

### Regenerating

```bash
cd agent_motivation_experiment
python3 paper_figures/fig_exp54_hour.py
```

## `intro_capacity.pdf` / `intro_capacity_curves.pdf` (2026-08-13 갱신, EXP-80)

**주장**: 도착한 요청의 90% 이상이 자기 지연 규칙을 지키는 최대 도착률이
**FluidServe v0.2 28.0 req/s, llm-d 19.9 req/s로 1.41배**, 가장 낮은 PolyServe(15.8)에
대해서는 **1.78배**다. 거절은 위반으로 세므로 전부 거절해서 이 값을 살 수 없다.
`paper_figures/fig_intro_capacity.py`.

**데이터**: 2026-08-08 워크로드 수정 **이후**의 정적 조건만, **다섯 arm × 여덟 도착률
(10/15/20/25/35/45/55/70 req/s) × 2반복 = 80 run**. 출처는 EXP-68·69·70(FluidServe·llm-d의
1반복), EXP-72(PolyServe·Llumnix SLO의 1반복), EXP-77(vLLM router 2반복),
**EXP-80(모자란 칸 23개를 채운 2반복)**.

**run 목록은 `paper_experiment/static_sweep_2026-08/manifest.tsv`에 고정돼 있다.** 그림을
다시 그리기 전에 `python3 paper_experiment/verify.py static_sweep_2026-08`를 돌린다 — glob에
새로 걸리는 run, `metrics.csv`의 변경, 채점의 변경 셋을 따로 검사한다.

| 포화 기준 | vLLM router | PolyServe | Llumnix SLO | llm-d | **FluidServe** | FluidServe/llm-d |
|---|---|---|---|---|---|---|
| 95% | 20.6 | 15.4 | 20.0 | 15.3 | **25.2** | 1.64 |
| **90%** | **21.4** | **15.8** | **20.4** | **19.9** | **28.0** | **1.41** |
| 80% | 22.9 | 16.6 | 21.3 | 22.1 | **33.7** | 1.53 |
| 70% | 24.5 | 17.4 | 22.1 | 24.2 | **39.2** | 1.62 |

⚠ **이 표는 2026-08-13에 바뀌었다. 그 전 값은 반복 하나 위에 있었다.**

| | 반복 1회 | 반복 2회 | 움직임 |
|---|---|---|---|
| llm-d 90% | 18.7 | **19.9** | **+1.2** |
| llm-d 95% | 12.2 | **15.3** | **+3.1** |
| llm-d 80% / 70% | 22.7 / 25.8 | 22.1 / 24.2 | −0.6 / −1.6 |
| FluidServe 90% | 28.1 | 28.0 | −0.1 |
| vLLM router · Llumnix SLO · PolyServe 90% | 21.4 / 20.4 / 15.8 | 21.4 / 20.4 / 15.8 | **0.0** |

**움직인 것은 llm-d 하나다.** 그 arm이 90% 선을 얕게 지나가기 때문이다 — 20 req/s에서 두
반복 평균이 89.9%로 선에 거의 정확히 걸쳐 있고 그 칸의 반복 폭이 2.6점이다. 나머지 셋은 선을
가파르게 지나가서(PolyServe는 15 req/s 99.9%에서 20 req/s 37.0%로 떨어진다) 반복이 늘어도
교차점이 그대로다. **교차점이 반복에 민감한지는 그 arm이 기준선을 어떻게 지나가느냐로 정해지고,
곡선을 보지 않으면 알 수 없다.**

⚠ **그래서 "기준선을 어디에 그어도 비가 1.49~1.52"라고 쓸 수 없게 됐다.** 지금은
**1.41~1.64**이고, 95%에서 가장 크고 90%에서 가장 작다. 쓸 수 있는 것은 **"네 기준 전부에서
FluidServe가 1.4배 이상 앞선다"**까지다.

⚠ **llm-d의 반복 폭이 포화 구간에서 유별나게 크다** — 25 req/s에서 12.0점, 35 req/s에서
15.1점이다(같은 구간 FluidServe는 1.1과 3.9, PolyServe·Llumnix SLO는 0.1 이하).
**그 구간의 llm-d 값을 한 번 재고 인용하면 안 된다.**

**캡션에 반드시 들어가야 하는 것 넷** (스크립트 docstring이 정본):

1. **arm이 둘이다.** PolyServe와 Llumnix SLO는 이 워크로드의 정적 조건이 없다. 5 baseline
   sweep이 채운다(격자는 EXP-70이 정했다).
2. **10~25 req/s가 1반복**이고 90% 통과가 FluidServe는 25~35, llm-d는 20~25 사이에서
   보간되므로 **두 값 다 1반복 점을 가로지른다.**
3. **swe를 llm-d만 m1f로 채점**한다(전체 시간 예산을 표현 못 해서). 채점 규칙 자체는 양쪽 다
   전체 30초다.
4. **예측선이 없다.** `motivation_capacity_is_a_policy.png`의 점선은 EXP-55에서 오는데
   그것은 수정 이전 워크로드다. 되살리려면 EXP-55 재측정이 필요하다.

⚠ **`motivation_capacity_is_a_policy_llmd.png`(수정 이전 워크로드)의 45.1 / 45.8과 같은 표에
넣으면 안 된다.** 거기서는 llm-d가 위인데, 그것은 다른 워크로드다.

---

## `motivation_four_panels.pdf` — 네 컨트롤플레인의 throughput / goodput / 달성률

**스크립트**: `fig_motivation_four_panels.py`. `--no-ours`를 주면 FluidServe를 빼고
`motivation_four_panels_noours.pdf`를 쓴다.
**크기**: 7.0 × 1.62 in, `figure*`에 `width=\textwidth`로 넣는다(스케일 1.0).

**무엇을 주장하는 그림인가.** 같은 네 엔진에서 컨트롤플레인만 바꾸면 **엔진이 만드는 토큰의
양보다 그중 규칙 안에 도착하는 양이 훨씬 크게 갈린다.** 45 req/s에서 총 출력은
6,847~13,077 tok/s(1.9배 폭)이고 goodput은 1,325~11,836(**8.9배 폭**)이다.

**데이터** (수정 후 워크로드, 정적 sweep 8 rate, 총 40조건):

| arm | 글롭 | 반복 |
|---|---|---|
| FluidServe (`fspfx`) | `*exp68s*`, `*exp68r*`, `*exp69*`, `*exp70*`의 `_fspfx_m1_rpm_*` | 35~70은 **2**, 10~25는 **1** |
| llm-d (`llmdslo`) | `*exp68s*`, `*exp68r*`, `*exp70*`의 `_llmdslo_m1f_rpm_*` | 35~70은 **2**, 10~25는 **1** |
| Llumnix SLO (`slo`) | `*exp72r1_slo_m1f_rpm_*` | 전 구간 **1** |
| PolyServe | `*exp72r1_polyserve_m1_rpm_*` | 전 구간 **1** |

**그려지는 값** (평균; 괄호는 두 반복이 있는 칸의 폭):

| req/s | | FluidServe | llm-d | Llumnix SLO | PolyServe |
|---|---|---|---|---|---|
| 20 | 총 출력 / goodput / offered | 9,095 / 9,040 / 99.6 | 8,762 / 8,383 / 88.7 | 9,106 / 8,994 / 95.4 | 9,017 / 4,159 / 39.9 |
| 45 | | 13,077 / 11,836 / 59.4 | 6,847 / 6,298 / 24.9 | 8,045 / 2,132 / 6.4 | 10,548 / 1,325 / 9.6 |
| 70 | | 13,952 / 11,897 / 38.2 | 7,102 / 6,663 / 17.7 | 8,825 / 747 / 1.3 | 8,443 / 424 / 1.2 |

⚠ **축 이름에 `variance`를 쓰면 안 된다.** 원래 통계의 이름이 total **variation** 거리라서
줄여 쓴 것처럼 보이는데, **이 값은 분산이 아니다** — 2차 모멘트가 아니고 단위가 요청 비율이지
비율의 제곱이 아니다. 그렇게 적으면 차이를 아는 독자가 "무엇의, 무엇에 대한 분산이냐"에서
멈춘다. **`deviation`은 기준에서 벗어난 정도를 가리키는 평범한 말이라 되묻지 않는다.**

⚠ **(a)의 축 이름이 `(normalized)`이므로 무엇으로 정규화했는지는 캡션이 말해야 한다** —
그 창의 최대값이다. 그림 안에서는 위쪽 점선이 1.0이라는 것이 그 말을 대신한다.

**캡션이 반드시 담아야 하는 것 넷.**

1. **음영이 없는 곡선이 더 정확한 것이 아니다.** 음영은 두 반복의 min..max이고, PolyServe와
   Llumnix SLO는 전 구간 1반복이라 음영이 아예 없다. **덜 정확한 쪽에 띠가 없다.**
2. **Llumnix SLO와 llm-d는 `m1f` 워크로드 설정을 받는다** — 두 정책 모두 전체 시간 예산을
   표현할 수 없어서다. 채점은 넷 다 전체 30초다.
3. **(a)를 "throughput은 정책과 무관하게 유지된다"로 읽으면 안 된다.** PolyServe는 35 req/s의
   10,628에서 70의 8,443으로 내려가고, llm-d는 25의 8,985에서 45의 6,847로 내려간다(그때
   거절률 71.5%). FluidServe만 단조 증가한다.
4. **goodput의 측정 구간은 0부터 마지막 도착까지다.** `exp23_rate_sweep.py`는 첫 도착부터
   재므로 이 trace에서 약 1.65% 크게 나온다.

**다시 만들려면**: `python3 paper_figures/fig_motivation_four_panels.py`

⚠ **`motivation_throughput_vs_goodput.pdf`와 같은 자리에 쓰지 않는다.** 그것은 수정 이전
워크로드의 세 정책이고, 그 워크로드는 부하 생성기가 모든 프롬프트를 정확히 12번씩 보내서
엔진 prefix hit rate가 83~86%로 부풀어 있던 상태다(고친 뒤 28.9%).

---

## `three_way_split.pdf` — 도착한 요청에 무슨 일이 있었나

**스크립트**: `fig_three_way_split.py`. **크기**: 3.335 × 1.75 in, 단일 열,
`width=\columnwidth`.

**무엇을 주장하는 그림인가.** 달성률과 거절률을 따로 실으면 읽는 사람이 뺄셈을 해야 하고,
**그 뺄셈이 두 실패를 가르는 자리다.** 막대 하나가 결과를 아는 도착 요청 전부이고, 셋으로
나뉜다 — 규칙 안에 완료 / 받았지만 못 지킴 / 거절. 첫 조각이 offered 달성률이라 논문의 다른
모든 달성률 수치와 맞는다.

| | 규칙 안 / 받았지만 못 지킴 / 거절 | 결과를 모름 | n |
|---|---|---|---|
| **한 시간 trace** | | | |
| FluidServe | 81.0 / **3.2** / 15.8 | 0.1% | 2 |
| llm-d | 56.7 / **4.6** / 38.7 | 0.1% | 2 |
| Llumnix SLO | 37.6 / **31.1** / 31.3 | 0.1% | 2 |
| PolyServe | 11.2 / **88.8** / **0.0** | **7.4%** | 2 |
| **정적 45 req/s** | | | |
| FluidServe | 59.4 / 4.5 / 36.0 | 1.7% | 2 |
| llm-d | 24.9 / 3.1 / 71.9 | 0.5% | 2 |
| Llumnix SLO | 6.4 / **20.9** / 72.8 | 2.5% | 1 |
| PolyServe | 9.6 / **90.4** / 0.0 | **35.9%** | 1 |

**캡션이 반드시 담아야 하는 것 셋.**

1. **막대에서 빠진 것이 있다** — run이 끝날 때 아직 처리 중이던 요청은 결과를 알 수 없어
   `attain()`이 두 분모에서 모두 빼고, 이 그림도 같게 뺀다. 그 비율이 한 시간에서 셋은
   0.1%인데 PolyServe만 7.4%이고, **정적 45 req/s의 PolyServe에서는 35.9%다.** 그 요청들은
   깊은 큐 뒤에 있던 것이므로 **빼는 것이 그 arm에 유리하다.**
2. **반복 횟수가 고르지 않다** — 한 시간은 네 arm 모두 2반복, 정적 45는 FluidServe와 llm-d가
   2반복이고 나머지 둘이 1반복이다.
3. **Llumnix SLO와 llm-d는 `m1f` 워크로드 설정을 받는다.** 채점은 넷 다 swe에 대해 전체
   30초다.

**다시 만들려면**: `python3 paper_figures/fig_three_way_split.py`

---

## `azure_rate_and_mix.pdf` — 도착률과 구성이 둘 다 움직이고, 서로를 예측하지 못한다

**스크립트**: `fig_azure_rate_and_mix.py`. **크기**: 3.335 × 2.45 in, 단일 열,
`width=\columnwidth`.

**무엇을 주장하는 그림인가.** `azure_trace_shape.pdf`는 도착률만 보여 주는데, motivation의
주장은 **도착률이 변하고 그 구성도 변한다**는 두 부분이다. 이 그림이 같은 나흘 창에 둘을
같이 놓는다. 구성이 변한다는 것이 **정적 클래스 파티션을 틀리게 만드는 것**이다 — 마루
도착률에 맞춰 함대를 준비해도 그때의 구성이 골에서의 구성이 아니다.

| 패널 | 무엇 | 실측 (Azure LLM Inference 2024, 나흘 창, 10분 구간) |
|---|---|---|
| (a) | 도착률 (정규화 — 그 창의 최대값으로 나눈다) | 골이 마루의 **17.3%**, 마루/골 **5.8배** |
| (b) | **Request mixture deviation** — 도착 요청 중 평균 구성과 어긋난 비율 | 중앙값 **11.5%**, p95 34.0%, **최대 40.2%** |

### (b)가 정확히 무엇인가

**그 구간의 구성과 창 평균 구성 사이의 total variation 거리**이고, 퍼센트로 적는다.
total variation에는 평범한 읽는 법이 하나 있고 그것이 이 값을 그릴 이유다 —
**지금 도착한 요청 중 몇 %가 클래스를 바꿔야 평균 구성과 같아지는가**이다(클래스별로 보면
지금 과잉인 클래스들의 초과분 합이다). **40%라고 읽히면 다섯 중 둘이 평균 구성에는 자리가
없는 클래스라는 뜻이고, 그 다섯 중 둘이 곧 평균에 맞춰 크기를 정한 파티션이 잘못 놓는
요청이다.**

기준을 평균으로 삼는 이유는 **고정된 구성을 하나 골라야 하는 배포가 고를 자연스러운 값**이기
때문이다. ⚠ **오차를 최소로 만드는 고정 구성이라고 주장하지는 않는다** — 그것은 다른
통계이고 이 주장에는 필요 없다.

### 구성 분해가 아니라 이 값을 그리는 이유 셋

1. **정적 파티션이 틀리는 양이 바로 이 값이다.** 평균 구성에 맞춰 크기를 정한 파티션은 매
   순간 정확히 이만큼의 요청을 잘못 놓는다. **y축이 trace의 성질이 아니라 그 설계가 지는
   오차다.**
2. **클래스 개수에 안 걸린다.** Azure 공개본은 요청 유형이 **둘**(대화형, 코드)이고 **우리
   워크로드는 셋**이다. 둘로 나눈 그림을 그리면 읽는 사람이 그것을 우리 셋에 대응시키려
   하는데, **그 대응은 존재하지 않는다** — 그 공개본에 deepresearch에 해당하는 것이 없다.
   바꿔야 하는 요청의 비율은 양쪽에서 같은 뜻이다.
3. **주장이 "믹스가 움직인다"이지 "코드 쪽으로 움직인다"가 아니다.** 이 파일의 앞 판은 반대로
   적었다 — 지표가 방향을 잃고 방향이 파티션 크기를 정하는 데 중요하다고. **그 반론은 다른
   주장에 대한 것이다.** 방향은 **어느** 파티션을 들 것인가를 물을 때 중요하고, 이 그림은
   **어떤 고정 파티션도 맞지 않는다**를 세우는 자리라 이 비율이 내용 전부다.

비율 자체를 잃지는 않는다 — 스크립트가 출력하고 아래에 적어 둔다. 코드 요청이 도착의
**3.9% ~ 68.0%**(p5 5.9 / 중앙값 23.2 / p95 61.8, **p95/p5 10.54배**)이고 **나머지 전부가
대화형**이다. 유효 클래스 수 `1/Σs²`는 최소 1.08 / 중앙값 1.55 / 최대 2.00이다.

**캡션이 반드시 담아야 하는 것 넷.**

1. **구성은 부하 수준의 함수가 아니다.** 두 계열의 상관이 **lag 0에서 +0.57**, ±12시간 안의
   어느 lag에서도 최대 **+0.63**이다. **부하 수준이 구성 분산의 약 40%만 설명한다.**
   ⚠ **"구성이 도착률보다 늦게 정점"이라고 쓰면 안 된다** — 재는 방법 둘이 어긋난다(하루별
   최대끼리는 구성이 0.8~8.3시간 늦고, 교차상관은 구성이 1.5시간 앞선다). 도착률의 하루별
   최대는 뾰족한 1분이라 안정적인 통계가 아니다.
2. **두 패널 다 원본 trace이고 우리가 재생하는 trace가 아니다.** 재생 trace의 도착률은 분위수
   변환을 우리가 고른 대역에 씌운 것이라 마루/골 비가 우리가 정한 값이고, 재생 trace의 구성은
   **세 클래스짜리 합성 스케줄**이다. **둘 다 그리면 순환이다.**
3. **Azure에는 요청 유형이 둘뿐이고 deepresearch에 해당하는 것이 없다.** 그러므로 이 그림은
   **구성이 움직인다는 근거이지, 우리가 고른 수준(10:2:1)이나 세 클래스 분해의 근거가
   아니다**(`motivation.md` §7.3.1의 한계와 같다).
4. **(b)의 값은 이 나흘 창의 평균 구성을 기준으로 한 것이다.** 다른 창을 잡으면 기준이 바뀌고
   값도 바뀐다. `traces/azure/mix_over_time.txt`의 168시간 통계와 같은 수가 아니다.

**색.** (b)는 채도를 낮춘 테라코타 `#b56349`이고, **가까운 두 색을 일부러 피한 것**이다.
`paper_style.ARM_COLOR`가 `#d62728`을 PolyServe에, `#ff7f0e`를 ablation arm 하나에 묶어
두었고 `exp27_figures`는 주황을 **deepresearch 클래스**에 쓴다. 둘 중 아무거나 쓰면 이 패널이
정책이나 클래스를 그린 것처럼 읽히는데 **이 그림은 워크로드의 성질이다.** 그리고 채도를 낮춘
이유는 (a)의 파랑 옆에 놓기 위해서다 — 처음에 PolyServe의 빨강을 그대로 썼더니 아래 패널이
위 패널에서 눈을 빼앗았다.

### `azure_mix_only.pdf` — 아래 패널만 남긴 판 (2026-09-10 추가)

**크기**: 3.335 × 1.40 in, 단일 열. 같은 스크립트가 같이 만든다. **같은 계열, 같은 창,
같은 10분 구간**이고 위의 모든 단서가 그대로 적용된다.

⚠ **도착률 패널이 빠지면 이 창의 부하가 5.8배 움직인다는 것을 그림이 말하지 않는다.**
두 부분 주장을 하는 자리에서는 stacked 판을 쓰거나 캡션이 도착률 범위를 말로 적어야 한다.
그리고 **"부하가 움직일 때 믹스도 움직인다"로 읽으면 안 된다** — 그 비교를 하라고 있는 것이
세로로 쌓은 판이다(둘의 상관은 +0.57이다).

⚠ **높이를 정하는 것은 곡선이 아니라 회전된 y축 이름이다.** `Workload mixture / deviation (%)`가
축을 따라 1.01 in이고 회전된 라벨은 캔버스로 잘리지 않으므로, 1.30 in에서는 첫 줄이 페이지
위로 0.01 in 넘어가 **조용히 잘렸다.** 1.40 in에서 축이 1.01 in가 되어 들어간다. 스크립트의
`check_ylabel`이 빌드마다 재서 여유를 찍으므로 높이를 바꾸면 즉시 드러난다.

**다시 만들려면**: `python3 paper_figures/fig_azure_rate_and_mix.py`
(네 판을 한 번에 만든다. 창은 `traces/dynamic/canonical/dyn60_azure4d.plan.json`에서 읽으므로
trace를 다시 만들면 그림도 따라간다.)

---

## `mix_shift.pdf` — 믹스 이동은 도착률이 보고하지 않는 용량 변화다

**주장**: 요청 개수 기준 클래스 비율이 chat 93%에서 균등으로 옮겨가면, **도착률이 그대로여도**
같은 도착률에서의 SLO 달성률이 FluidServe 34.6점, llm-d 48.8점 내려간다.

**데이터**: EXP-93, 4 run = 2 arm(`fspfx`, `llmdslo`) × **2반복**, trace
`traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045`. 전부 2026-08-23, 바이너리 `89d9593b`,
엔진 stock, 게이트웨이 `GOMAXPROCS=16`. **한 세션이다.**
채점은 `analysis_scripts/request_level/exp93_mix_shift.py`를 import해서 쓴다(재구현 아님) —
로더 수정이 그림에 도달하게 하기 위해서다. 표 전문 `results/aggregate_analysis/exp93/report.md`.

**패널**

- **(a)** 한 시간 시계열. 회색 면이 **도착률**이고, 세로 점선이 믹스 구간 경계, 위 숫자가
  그 구간의 chat 비중이다. 도착률을 같이 그린 이유는 그것이 **통제**이기 때문이다 — 두 arm과
  두 trace의 도착 시각이 동일하므로, **구간 경계에서 수준이 이동하는 것은 믹스이고 구간 안에서
  출렁이는 것은 도착률**이라는 것을 읽는 사람이 직접 확인할 수 있다. 곡선은 60초 창을 세 칸
  이동평균한 것이다.
- **(b)** 같은 run을 rate sweep으로 다시 읽은 것. 창 하나가 그 창이 본 도착률에서의 관측
  하나이므로 **chat 93% 구간과 균등 구간을 같은 도착률에서 비교**할 수 있다. 실선과 점선의
  세로 간격이 믹스가 옮긴 용량이다. **경계에 걸친 창은 양쪽에서 제외**했다 — 두 믹스를 담은
  창은 어느 쪽의 관측도 아니다.

**표와 대조했다.** (b)의 여섯 점이 `report.md`의 H1 표와 일치한다(FluidServe 균등
83.0 / 75.8 / 69.1 / 59.5 / 50.5 / 47.3, llm-d 균등 65.4 / 52.0 / 41.7 / 35.6 / 29.6 / 27.7).
(a)의 첫 구간에서 FluidServe가 93까지 내려가는 것이 창별 최솟값 91.7과 맞는다.
⚠ **작게 렌더해서 읽으면 (a)를 잘못 읽는다** — y축 상한이 118이라 100 눈금이 축 위쪽에 있어서,
FluidServe의 93이 70대로 보인다. 300 dpi로 왼쪽 패널만 잘라서 확인했다.

**캡션에 반드시 들어가야 하는 것 넷**

1. **arm당 반복 2회**, 한 세션. `fspfx` 반복 폭 0.4점, `llmdslo` 0.9점.
2. **offered 분모** — 도착한 모든 요청이 분모이고 거절은 위반이다. 거절률은 FluidServe 18.9%,
   llm-d 45.6%.
3. **stress test이지 현실적 워크로드가 아니다.** 믹스 이동 폭이 Azure 2024가 뒷받침하는
   범위(conv/code 비 4.71배)를 넘는다.
4. **균등 구간에서 swe의 prompt 재사용이 부풀려져 있다** — transcript가 1,500건이라 그 구간에서
   사본 간격이 약 2.7분이고 스케줄러 prefix 색인 보유 시간(약 2.8분) 안이다. 이것은 그 구간을
   **실제보다 좋게** 만들므로 **그림이 보이는 격차는 참값의 하한이다.**

**이 그림이 말하지 않는 것**

- **s0와 s3의 무릎(약 48, 약 20 req/s)은 측정값이 아니다** — m1의 측정된 무릎 28.0 req/s를
  요청당 입력 토큰 수에 반비례로 환산한 추정치다.
- **(a)의 마지막 구간은 믹스와 도착률이 함께 나빠진다** — chat 60%이면서 그 구간의 도착률
  중앙값이 35.8 req/s로 네 구간 중 가장 높다. **그 구간의 하락을 믹스만으로 돌리면 안 된다.**
  믹스만 분리해서 보는 것은 (b)이고, 그것이 (b)가 있는 이유다.
- **선호(class affinity)를 끈 조건은 이 그림에 없다.** 네 run 모두 배포 기본값(켬)이다.

**다시 만들려면**: `python3 paper_figures/fig_mix_shift.py`

---

## `motivation_throughput_vs_goodput_4panel.pdf` — 같은 측정을 네 패널로

`fig_motivation_throughput_goodput.py`의 `build_four()`가 그린다. 세 패널판
(`motivation_throughput_vs_goodput.pdf`)과 **같은 `collect()` 통과분에서 나오므로 채점이
같다** — `EXP22.load_run`과 `EXP22.per_request`가 같은 파일을 같은 규칙으로 읽고, 이 함수는
모아 둔 다섯 양 중 넷을 골라 순서를 정할 뿐이다. 같은 arm·같은 도착률의 값을 두 그림에서
읽으면 같은 수가 나온다.

| 패널 | 무엇 | y축 |
|---|---|---|
| (a) Goodput Tokens | 자기 규칙 안에서 완료된 요청이 만든 출력 토큰 | Tokens/s (b와 공유) |
| (b) Throughput | 엔진이 만든 출력 토큰 전부 | Tokens/s (a와 공유) |
| (c) Request SLO (admitted) | 정책이 **받아들인** 요청을 분모로 한 달성률, 요청 단위 | % (d와 공유) |
| (d) Rejection rate | 도착한 요청 중 거절된 비율 | % (c와 공유) |

**왜 이 순서인가.** (a)가 이 그림이 주장하는 양이고 (b)가 바로 옆에서 같은 y축을 쓰므로,
엔진이 만든 토큰의 양은 정책 간에 비슷한데 그중 값을 하는 부분은 자릿수가 다르다는 것이
축을 다시 재지 않고 보인다. (c)와 (d)는 같은 질문을 토큰이 아니라 **요청 수**로 센 것이고,
둘 다 같은 도착 집합에 대한 백분율이라 역시 축을 공유한다.

**(c)와 (d)는 반드시 같이 읽는다.** (c)는 받아들인 것만 분모로 하므로 **거의 아무것도
받지 않는 정책에 최고점을 준다.** (d)가 그 옆에 있는 이유가 그것이고, 세 패널판이 못 하는
짝이다 — 거기서는 둘째 분모가 점선 겹침으로 실려서 거절량을 두 곡선의 간격으로 추론해야 한다.

⚠ **(d)에 곡선이 둘뿐인데 범례는 넷이다.** PolyServe와 vLLM router는 admission control이
없어서 여덟 도착률 전부에서 거절률이 0.0%다. 둘을 그리면 x축 위에서 서로 겹쳐 마지막에
그린 것만 색이 보이고, 읽는 사람은 다른 arm이 0인지 빠진 것인지 구별할 수 없다. 그래서
그리지 않는다 — **캡션이 "그 둘은 어느 도착률에서도 거절하지 않는다"를 반드시 적어야 하고,
그것은 데이터가 없는 것이 아니라 정책의 성질이다.**

**x축 눈금은 실제로 측정한 도착률이다.** 네 패널을 7.0인치에 놓으면 패널당 그리는 폭이 약
1.4인치라 8 pt로 일곱 개 라벨이 안 들어간다. 라벨을 다는 것은 10 / 25 / 45 / 70이고 나머지
측정 도착률(15 / 20 / 35 / 55)은 라벨 없는 minor tick이다. 세 패널판은 10부터 70까지 10
간격으로 라벨을 다는데 그중 넷(30 / 40 / 50 / 60)은 **측정한 적이 없는 값**이고, 이 그림은
그것을 만들지 않는다.

### ⚠ 이 그림만 pinned 사본에서 그린다 — 선택이 형제 그림들과 다르다

**`results/`가 아니라 `paper_experiment/static_sweep_clean_2026-08/data/`에서 읽는다.**
2026-08-26 디스크 정리가 manifest가 참조하는 run만 남기고 나머지를 비웠는데, EXP-86 조건들은
**`results/` 쪽 사본이 비워지고 pinned 사본만 남았다** — 디렉토리 24개가 아직 있고 안이
비어 있다. 그래서 `results/`를 glob하면 대부분의 도착률에서 반복이 둘이 아니라 하나가 되고,
**세 칸은 아예 사라진다**(vLLM 45 req/s, PolyServe 70, Llumnix SLO 55). 빠지는 것이 조용해서
곡선이 짧아진 것으로만 보인다. llm-d는 EXP-82라 정리 대상이 아니었고 영향이 없다.

`pinned_arms()`가 arm 이름·색·마커·순서를 그대로 두고 **run glob만 바꾼다. 채점은 하나도
바뀌지 않는다** — 같은 `load_run`이 같은 파일을 아직 갖고 있는 디렉토리에서 읽는다.

⚠ **그러므로 이 그림과 세 패널 형제들은 지금 같은 집합에서 그려지지 않았다.** 형제들은
아직 `results/` 선택을 쓴다. **한쪽에서 읽은 수를 다른 쪽에서 읽은 수 옆에 인용하면 안
된다.** 형제들의 선택도 같이 pinned로 옮기면 이 경고는 없어진다.

### 데이터

`paper_experiment/static_sweep_clean_2026-08/manifest.tsv`가 정본이다 — arm마다 **16 run**
(8 도착률 × **2반복**), 조건당 8분, 엔진 넷, 각 `metrics.csv`의 sha256이 함께 적혀 있다.
네 arm 전부 8/8 칸이 n=2다. **오차막대는 그리지 않았다** — 값은 두 반복의 평균이고, 이
워크로드의 세션 간 이동은 최대 4.6점으로 측정돼 있다.

swe는 설정이 둘이고 채점은 하나다: llm-d와 Llumnix SLO는 전체 시간 예산을 표현할 수단이
없어 `m1f` 워크로드 설정을 쓰고, PolyServe와 vLLM router는 `m1`을 쓰며, **네 arm 모두 전체
시간 30초 규칙으로 채점된다.**

⚠ **EXP-108이 끝나면 이 그림은 그대로 쓸 수 없다.** 그 실험이 swe의 약속을 전체 시간 30초에서
per-token(첫토큰 7초 + 토큰당 75 ms)으로 바꿔 네 arm을 다시 재고 있으므로, **t75로 채점한
어떤 열도 위 표와 같은 표에 못 들어간다.**

### 그려진 값 (평균, n=2)

| arm | req/s | 10 | 15 | 20 | 25 | 35 | 45 | 55 | 70 |
|---|---|---|---|---|---|---|---|---|---|
| vLLM | goodput (tok/s) | 5170 | 7717 | 10045 | 9000 | 1371 | 778 | 762 | 579 |
| PolyServe | goodput (tok/s) | 5153 | 7689 | 4711 | 4647 | 2269 | 1516 | 592 | 549 |
| Llumnix SLO | goodput (tok/s) | 5137 | 7664 | 9904 | 4602 | 3030 | 2489 | 1538 | 795 |
| llm-d | goodput (tok/s) | 4819 | 7355 | 9448 | 9148 | 8311 | 6601 | 6949 | 7565 |
| vLLM | throughput (tok/s) | 5218 | 7829 | 10310 | 12691 | 14291 | 14303 | 13981 | 13191 |
| PolyServe | throughput (tok/s) | 5218 | 7825 | 10220 | 11194 | 11876 | 11750 | 11119 | 9609 |
| Llumnix SLO | throughput (tok/s) | 5201 | 7825 | 10293 | 12528 | 11738 | 9033 | 9123 | 9979 |
| llm-d | throughput (tok/s) | 4918 | 7774 | 10092 | 10156 | 9178 | 7259 | 7857 | 8322 |
| vLLM | admitted (%) | 100.0 | 99.8 | 98.9 | 70.9 | 7.9 | 3.8 | 3.4 | 2.6 |
| PolyServe | admitted (%) | 100.0 | 99.9 | 41.8 | 30.7 | 16.1 | 9.3 | 2.0 | 1.5 |
| Llumnix SLO | admitted (%) | 99.6 | 98.0 | 94.8 | 33.2 | 20.4 | 25.0 | 15.5 | 7.5 |
| llm-d | admitted (%) | 98.7 | 95.0 | 93.3 | 89.5 | 89.3 | 89.4 | 86.9 | 91.2 |
| vLLM | rejected (%) | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| PolyServe | rejected (%) | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Llumnix SLO | rejected (%) | 0.0 | 0.0 | 0.0 | 0.0 | 39.4 | 71.1 | 78.4 | 81.9 |
| llm-d | rejected (%) | 6.1 | 0.2 | 1.9 | 23.2 | 52.5 | 76.5 | 77.7 | 81.9 |

### 캡션이 반드시 적어야 하는 것

- **(c)는 admitted 분모다.** (d)와 함께 읽어야 뜻이 있다.
- **PolyServe와 vLLM router는 (d)에 없고, 그것은 두 정책이 어느 도착률에서도 거절하지 않기
  때문이다.**
- **"vLLM"은 PyPI `vllm-router` 패키지의 기본 `cache_aware` 정책**(SGLang model gateway의
  fork)이고 vllm-project/production-stack이 아니다. 같은 이름의 물건이 둘이라 축의 짧은
  라벨이 그 구별을 못 실으므로 캡션이 어느 쪽인지 적어야 한다.
- **거절하지 않는 두 arm은 다른 종류의 사라진 요청을 만들고 그것은 그림에 없다.** admission
  control이 없는 정책은 요청을 거절하는 대신 측정 창이 닫힐 때 미완으로 남기고, 미완 요청은
  결과가 없어 `attain()`이 **두 분모 모두에서** 뺀다. 그 비율이 35 / 45 / 70 req/s에서 vLLM
  router 21.8 / 39.6 / 61.6%, PolyServe 37.9 / 35.9 / 35.7%인 반면 llm-d 1.0 / 0.5 / 0.3%,
  Llumnix SLO 3.8 / 2.5 / 2.2%다. **즉 이 제외는 거절하지 않는 두 arm에 유리하다.**
- FluidServe는 이 그림에 **일부러 없다.** 논문 자신의 시스템이 있어야 성립하는 motivation
  그림은 motivation 그림이 아니다.

**다시 만들려면**: `python3 paper_figures/fig_motivation_throughput_goodput.py`
(이 파일 하나가 형제 그림들도 같이 다시 그린다)

## `motivation_throughput_vs_goodput_4panel_t75.pdf` / `_t75_withfs.pdf` — 다섯 컨트롤플레인, 토큰 단위 누적 deadline 채점

**스크립트**: `fig_motivation_tg_4panel.py`. 한 번 실행하면 둘 다 쓴다 — FluidServe를 뺀 판
(motivation용)과 넣은 판. 채점 규칙은 고정이고 이 스크립트에 선택지가 없다.
**크기**: 7.0 × 1.75 in, `figure*`에 `width=\textwidth`(스케일 1.0). 페이지 크기 504 × 126 pt 확인.
한 번 실행하면 판이 다섯 나온다 — 기본 둘, `_wide` 둘(같은 값을 덜 정사각으로),
그리고 **`_withfs_col.pdf`(2026-09-10 추가)**.

### `_withfs_col.pdf` — 한 칼럼, 2×2 블록

**크기 3.335 × 2.85 in, `figure`에 `width=\columnwidth`.** 같은 데이터·같은 채점·같은 y 한계이고
바뀐 것은 배치와 색과 글씨 크기뿐이다. **한 칼럼에서는 네 패널을 한 줄로 놓을 수 없다** —
패널 하나가 0.6 in 폭이 되고 회전된 y축 이름이 패널보다 길어진다. 그래서 **(a)와 (b)를 윗줄에**
(둘이 y축을 공유하므로 서로 견줄 수 있어야 한다), (c)와 (d)를 아랫줄에 놓는다. 패널 축 상자는
1.24 × 0.79 in.

- **색은 PuBuGn 5단계**(`RAMP`), `fig_two_models_hour.py --ramp`와 같은 램프·같은 순서다.
  ⚠ **순차 램프인데 arm에는 순서가 없다** — 밝기 순서를 성능 순서로 읽으면 안 된다.
  ⚠ **가장 옅은 단계가 사실상 흰색**이라 0.9 pt 선이 사라지므로 **선 아래에 회색 테두리**를
  깔았다(색 자체는 요청받은 값 그대로다). 두 옅은 arm은 선 색으로는 거의 안 갈리고 **마커
  모양(오각형 대 원)이 실제로 가르는 채널**이다.
- **패널의 눈금·축 글씨가 8 → 6 pt**(`shrink=2.0`). 범례는 6.5 pt 2줄이고, 다섯 이름은 한
  칼럼에 한 줄로 안 들어간다.
- ⚠ 6 pt는 본문 8 pt보다 작으므로 **`width=\columnwidth`로 배율 없이** 넣어야 한다.

| 패널 | 무엇 | y축 |
|---|---|---|
| (a) Goodput tokens | 자기 규칙 안에서 완료된 요청이 만든 출력 토큰 | Tokens/s (b와 공유) |
| (b) Throughput | 엔진이 만든 출력 토큰 전부 | Tokens/s (a와 공유) |
| (c) Request SLO (admitted) | 정책이 **받아들인** 요청을 분모로 한 달성률, 요청 단위 | % |
| (d) Rejection rate | 도착한 요청 중 거절된 비율 | % |

**(c)와 (d)는 반드시 같이 읽는다.** (c)는 받아들인 것만 분모로 하므로 거의 아무것도 받지 않는
정책에 최고점을 준다. 모든 도착을 분모로 한 곡선은 그리지 않는다 — (c)와 (d)가 그것을 이미
담고 있어서 세 번째 표현이 된다.

**x축은 선형이고 라벨만 등간격이다 (2026-09-01 사용자 결정).** 도착률은 양이므로 10에서 15까지의
거리가 55에서 70까지의 거리의 3분의 1로 그려진다 — 등간격 범주형 축은 그 두 단계가 같은 크기라고
주장하게 된다. **라벨은 10 req/s 간격(10·20·…·70)으로 놓는데**, 측정한 도착률
(10·15·20·25·35·45·55·70)은 단계 크기가 셋(5·10·15)이라 그것으로 라벨을 달면 넷이 축 앞 3분의 1에
몰리기 때문이다.
⚠ **그 일곱 라벨 중 넷(30·40·50·60)은 측정한 적이 없는 값이다.** 등간격 축을 위해 치르는 실제
비용이고, 그래서 **측정한 도착률에는 라벨 없는 minor tick을 남기고** 모든 곡선이 측정 지점마다
마커를 갖는다 — 어디가 측정이고 어디가 그 사이를 이은 선인지는 그것으로 읽는다.

⚠ **이름의 `t75`는 장식이 아니다.** `fig_motivation_throughput_goodput.py`가 이미
`motivation_throughput_vs_goodput_4panel.pdf`를 쓰는데, 그것은 이전 정적 sweep의 pinned 사본에서
그려지고 **swe를 전체 시간 30초로 채점**한다. 두 그림의 수치는 서로 옆에 놓을 수 없으므로 파일
이름을 공유하면 안 된다.

### 채점 규칙

토큰당 절반을 **누적 deadline + 관용**으로 판정한다. `a_i`는 i번째 출력 토큰이 제출 시각으로부터
도착한 시각, `T`는 클래스의 첫 토큰 예산, `P`는 토큰당 예산이다.

```
|{i : a_i <= T + i*P}| / N >= 0.95   (i 는 0부터, 첫 토큰 마감 = T)
```

첫 토큰 조건을 별도 논리곱으로 남기는 이유: 스케줄이 도착 시각에서 시작하면 `T`를 안 쓴 만큼이
디코드로 이월되어, 엔진이 `P`보다 빠르면 요청 하나가 약 `N × (P − 실제 토큰당 시간)`의 첫 토큰
누적 deadline 자체는 PolyServe·QoServe·JITServe의 규칙이고 **관용은 그 아홉 편 어디에도 없는
이 프로젝트의 추가**다. 관용을 90%로 뒀을 때의 표와 여덟 규칙 비교는
`results/aggregate_analysis/token_deadline_2026-08-31/README.md` §8에 남아 있다(**그 수치는 지금
규칙의 수치와 같은 표에 넣지 않는다**).

클래스 예산: chat (5 s, 50 ms) · deepresearch (10 s, 100 ms) · **swe (7 s, 75 ms)**.

### 데이터

| arm | 결과 디렉토리 | 반복 | 정책이 받은 swe 약속 |
|---|---|---|---|
| vLLM-router (`vllmcache`) | `results/*exp77r[12]_vllmcache_m1_rpm_*` | 70 req/s만 **1**, 나머지 2 | 읽지 않는다 |
| PolyServe (`polyservept75`) | `results/*exp108r[12]_polyservept75_t75fair_rpm_*` | 전 구간 **2** | `t75fair`의 `slo.swe.tbt_ms=75` |
| Llumnix SLO (`slot75`) | `results/*exp108r[12]_slot75_t75fair_rpm_*` | 전 구간 **2** | 같음 |
| llm-d (`llmdslot75`) | `results/*exp108r[12]_llmdslot75_t75fair_rpm_*` | 전 구간 **2** | 같음 |
| FluidServe (`fsv3capgnofrct75`) | `results/*exp108r[12]_fsv3capgnofrct75_t75_rpm_*` | 전 구간 **2** | `--fluidserve-class-budgets 25:decode:75` |

⚠ **오차 띠를 그리지 않는다 (2026-09-03 지시).** 두 반복의 min..max를 음영으로 깔았었는데,
네 패널을 7인치에 놓으면 다섯 arm의 띠가 정작 그림을 읽는 구간에서 겹쳐 곡선을 가린다.
**그 폭은 사라지지 않고 이 그림의 CSV에 남아 있다** — 그려지는 네 양마다 `*_min` / `*_max`
열이 있고, `n_repeats` 열이 칸마다 반복 수다(vLLM-router 70 req/s만 1, 나머지 39칸은 2).

⚠ **(d)에 vLLM-router가 없고, 범례는 다섯인데 곡선은 넷이다 (2026-09-03 지시).** 그 arm은
여덟 도착률 전부에서 거절률이 0.0%라 패널 내내 x축 위에 눕는데, 거기서는 계열이 아니라 두 번째
축선으로 읽히고 그 아래 그려지는 arm을 가린다. **캡션이 "그 arm은 어느 도착률에서도 거절하지
않는다"를 반드시 적어야 한다 — (d)에 없는 것은 데이터가 없어서가 아니라 정책의 성질이다.**
빠지는 것은 (d) 하나뿐이고 나머지 세 패널과 범례에는 그대로 있다. 스크립트는 그 arm의 이름을
매 실행마다 찍으므로 이 생략은 조용할 수 없고, 거절률이 0이 아닌 arm은 자동으로 다시 그려진다.

**arm은 색과 마커 모양 둘로 구분한다 (2026-09-03 지시).** vLLM-router 육각형 · PolyServe 원 ·
Llumnix SLO 삼각형 · llm-d 마름모 · FluidServe 사각형으로, `fig_intro_capacity.py`와 같은
배정이라 한 arm이 논문 전체에서 한 모양이다. 선 종류는 다섯 arm 모두 실선 그대로다 — 채널을
하나 더 쓰는 이유는 흑백으로 인쇄하거나 두 곡선이 교차할 때 남는 것이 모양뿐이기 때문이다.

⚠ **vLLM-router만 다른 세션이고, 그것은 실제 결합이다.** EXP-108의 arm이 넷이고 거기 없으므로
EXP-77(2026-08-10)에서 가져왔다. **약속을 읽지 않는 유일한 arm이라 다른 약속으로 재채점하는 것이
정당하다** — 라우팅이 prefix cache affinity뿐이라 워크로드 파일의 swe 예산이 무엇이든 그 arm이 본
요청은 같다. 그러나 세션의 나머지는 같지 않다: 3주 앞이고, 같이 그려지는 arm들의 스케줄러
바이너리가 다르며, **그 사이 2026-08-28에 장비가 초기화되고 복원됐다.** 이 워크로드의 세션 간
이동은 최대 4.6점으로 측정돼 있는데 **그 측정은 복원을 사이에 두지 않는다.**

### 그려진 값 (반복 평균, 규칙 `ladder95`)

⚠ **정본은 그림 옆의 CSV 다** (`motivation_throughput_vs_goodput_4panel_t75[_withfs].csv`,
그림을 그리는 같은 실행이 같은 집계에서 쓴다). 아래 표는 손으로 옮긴 것이라 그림을 다시
그리면 낡는다 — 실제로 2026-09-02 에 llm-d 의 10 req/s 네 칸이 낡은 채로 남아 있었다.
값을 인용할 때는 CSV 를 연다.

| arm | 양 | 10 | 15 | 20 | 25 | 35 | 45 | 55 | 70 |
|---|---|---|---|---|---|---|---|---|---|
| vLLM-router | goodput (tok/s) | 5,168 | 7,704 | 10,073 | 11,826 | 1,946 | 943 | 702 | 225 |
| PolyServe | goodput (tok/s) | 4,890 | 7,207 | 7,785 | 7,890 | 5,654 | 4,907 | 4,764 | 4,362 |
| Llumnix SLO | goodput (tok/s) | 5,150 | 7,693 | 10,038 | 7,681 | 4,095 | 1,158 | 411 | 273 |
| llm-d | goodput (tok/s) | 5,040 | 7,531 | 9,755 | 8,686 | 8,524 | 9,223 | 7,762 | 7,092 |
| FluidServe | goodput (tok/s) | 5,132 | 7,528 | 9,904 | 11,940 | 13,336 | 15,099 | 14,399 | 14,875 |
| vLLM-router | throughput (tok/s) | 5,223 | 7,812 | 10,292 | 12,682 | 14,374 | 14,299 | 14,105 | 13,436 |
| PolyServe | throughput (tok/s) | 5,115 | 7,718 | 10,220 | 12,525 | 14,773 | 15,084 | 15,219 | 14,780 |
| Llumnix SLO | throughput (tok/s) | 5,217 | 7,803 | 10,287 | 12,565 | 11,724 | 8,981 | 9,034 | 9,884 |
| llm-d | throughput (tok/s) | 5,172 | 7,783 | 10,053 | 9,260 | 8,927 | 9,708 | 8,148 | 7,683 |
| FluidServe | throughput (tok/s) | 5,204 | 7,744 | 10,213 | 12,369 | 13,881 | 15,607 | 14,956 | 15,457 |
| vLLM-router | admitted (%) | 100.0 | 100.0 | 100.0 | 98.6 | 14.0 | 6.3 | 4.3 | 1.4 |
| PolyServe | admitted (%) | 99.2 | 95.5 | 86.1 | 74.8 | 52.2 | 44.3 | 42.4 | 37.7 |
| Llumnix SLO | admitted (%) | 100.0 | 100.0 | 100.0 | 72.7 | 35.5 | 13.8 | 5.8 | 3.2 |
| llm-d | admitted (%) | 99.9 | 98.7 | 99.3 | 96.6 | 98.4 | 97.9 | 98.5 | 93.7 |
| FluidServe | admitted (%) | 100.0 | 100.0 | 100.0 | 100.0 | 99.9 | 99.9 | 99.9 | 99.8 |
| vLLM-router | rejected (%) | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| PolyServe | rejected (%) | 0.0 | 0.0 | 0.0 | 0.4 | 10.5 | 27.6 | 40.5 | 54.7 |
| Llumnix SLO | rejected (%) | 0.0 | 0.0 | 0.0 | 0.0 | 39.2 | 70.8 | 78.5 | 81.9 |
| llm-d | rejected (%) | 0.0 | 0.0 | 2.4 | 32.7 | 54.6 | 61.0 | 75.3 | 82.6 |
| FluidServe | rejected (%) | 0.0 | 0.1 | 0.2 | 2.6 | 22.2 | 30.0 | 46.9 | 56.1 |

### 캡션이 반드시 적어야 하는 것

- **swe가 per-token 형태(첫 토큰 7초 + 토큰당 75 ms)로 채점된다.** 전체 시간 30초로 채점한 어떤
  수치와도 같은 표·같은 문장에 넣지 않는다.
- **(c)는 받아들인 요청만 분모로 한다.** (d)와 함께 읽어야 뜻이 있다.
- **vLLM-router는 어느 도착률에서도 거절하지 않는다.** (d)에서 x축 위에 놓이는 것은 데이터가
  없어서가 아니라 admission control이 없기 때문이고, 그래서 그 arm의 (c)는 모든 도착을 분모로
  한 값과 같다.
- **vLLM-router만 EXP-77(2026-08-10)이고 나머지 넷은 EXP-108(2026-08-31)이다.** 그 사이에 장비
  초기화가 있었고 그 결합은 측정되지 않았다.
- **llm-d의 10 req/s 칸은 두 반복이 갈린다**(거절 0.0% 대 42.0%). 둘 다 `run_health.py`를
  통과했으므로 인프라 고장이 아니라 조건마다 새로 학습되는 예측기의 성질이다. **그 칸의 넓은
  띠가 그것이고 평균선 하나로 읽으면 안 된다.**
- **무릎 아래(10~20 req/s)에서는 다섯 arm이 전부 100 부근이다.** 그 구간의 차이는 순위가 아니라
  천장이다.
- **관용을 90%로 낮췄을 때의 이득은 FluidServe +0.0점, PolyServe +2.6점(최대 5.2)이다** —
  관용이 우리에게 유리한 잣대라는 반론은 측정으로 닫힌다. 지금 고정된 값은 95%다.

### ⚠ llm-d 의 10 req/s 칸은 EXP-108 의 두 반복이 아니다 (2026-09-02)

**그린 표는 `exp108_paper_per_run.csv` 이고, 그것은 `exp108_per_run.csv` 와 한 칸이 다르다.**
EXP-108 의 llm-d 10 req/s 두 반복이 **거절률 0.0% 와 42.0%** 로 갈렸다 — 같은 워크로드, 도착
요청 수 4,618 대 4,611, 엔진 4/4 인데 offered 달성률이 40 점 넘게 벌어졌다. 나머지 일곱 도착률의
반복 간 편차는 0.1~4.0 점이므로 그 칸에만 있는 현상이다.

**EXP-110 이 그 도착률만 세 번 더 쟀고 세 번 다 거절 0.0% 였다** (offered 97.8 / 99.5 / 98.6,
누적 규칙 99.5 / 99.9 / 100.0, goodput 4,970~5,159 tok/s). 다섯 run 중 넷이 거절 0% 이므로
**42.0% 반복은 다섯 번에 한 번 일어난 outlier** 이고, 원인은 밝히지 못했다.

→ **이 칸은 EXP-110 반복 3 과 5 로 그린다**(사용자 결정, 2026-09-02). 다른 모든 칸과 같이
n=2 를 유지한다. 바뀐 값은 그 칸 하나뿐이고 나머지 31 칸은 바이트 단위로 동일하다:

| | 이전 (EXP-108 반복 1·2) | 지금 (EXP-110 반복 3·5) |
|---|---|---|
| goodput | 3,981 (밴드 2,895~5,067) | **5,040** (밴드 4,990~5,091) |
| throughput | 4,077 (2,975~5,180) | **5,172** (5,169~5,175) |
| admitted | 99.48 (98.95~100.0) | 99.90 (99.80~100.0) |
| 거절률 | 21.0 (0.0~42.0) | **0.0** |

**말하지 않는 것**: 왜 그 한 번이 42% 를 거절했는가. EXP-108 은 학습 서버 로그를 마지막 2,000 줄
(= 약 80 초)만 저장해서 거절이 일어난 1~6 분 구간의 기록이 없고, EXP-110 이 그 계측을 고쳤지만
세 번 다 정상 run 이라 비교 대상이 없다. 전체 경위와 반증 조건은
`experiments/EXP-110_llmd-lowrate-split.md`. 이전 표를 보려면
`fig_motivation_tg_4panel.py --csv <...>/exp108_per_run.csv`.

**다시 만들려면**: `python3 paper_figures/fig_motivation_tg_4panel.py`
(채점 표가 먼저 있어야 한다: `rescore_token_deadline.py --tag exp108 --runs <64개>`,
`--tag exp110_llmd10 --runs <EXP-110 반복 3·5>` 뒤에 두 표를 합쳐
`exp108_paper_per_run.csv` 를 만든다, `--tag vllm77 --runs <EXP-77 vllmcache 15개>`)

### `_wide.pdf` / `_withfs_wide.pdf` — 같은 네 패널을 덜 정사각형으로

같은 스크립트가 같은 값으로 한 쌍을 더 낸다. **데이터·스타일·y 범위가 전부 같고 다른 것은
캔버스 높이와 패널 사이 간격 둘뿐**이다.

| | 캔버스 | 패널 사이 간격 | **패널 축 상자** | 가로세로비 |
|---|---|---|---|---|
| 기본 | 7.0 × 1.75 in | `w_pad=2.0` | 1.13 × 1.05 in | **1.08** (거의 정사각형) |
| `_wide` | 7.0 × **1.52 in** | `w_pad=0.9` | **1.22 × 0.82 in** | **1.49** |

폭은 `\textwidth`에 고정돼 있으므로 **패널을 좌우로 길게 만드는 방법은 높이와 간격을 줄이는
것뿐**이다. 값을 바꾸려면 스크립트의 `FIG_H_WIDE`와 `W_PAD_WIDE` 둘만 고치면 되고, 스크립트가
매 판마다 **패널 축 상자의 실제 크기를 in 단위로 찍는다** — "패널이 너무 정사각형이다"는 그
숫자에 대한 말이지 다른 무엇에 대한 말이 아니므로, 눈으로 재지 않고 그 줄을 읽는다.

⚠ **(c)의 y축 이름을 `SLO attainment (%)`에서 `Attainment (%)`로 줄였다.** 회전한 축 이름이
패널 높이보다 길면 잘리는데, wide 판의 패널은 0.82 in이고 그 문구는 약 1.05 in이다. **글꼴을
줄이지 않고 문구를 줄인 것**이고, 그 패널이 무엇을 재는지는 바로 아래 캡션
`(c) Request SLO (admitted)`가 이미 말한다.

**CSV는 따로 두지 않는다** — 기본 판 옆의 `..._t75.csv` / `..._t75_withfs.csv`와 같은 값을
그리고, 같은 표를 두 번 쓰는 것이 두 파일이 어긋나기 시작하는 방식이다.



## `outcome_split_sweep_t75.pdf` — 도착한 요청 전부에 무슨 일이 있었나, 도착률별로

**스크립트**: `fig_outcome_split_sweep.py`. **크기**: 7.0 × 1.90 in, `figure*`에
`width=\textwidth`(스케일 1.0). **값**: 같은 basename의 `outcome_split_sweep_t75.csv`
(40행 = 5 arm × 8 도착률, 각 띠의 평균과 반복 간 min/max, 칸별 반복 수, 채점 규칙).

컨트롤플레인마다 패널 하나, 도착률마다 누적 막대 하나. **막대 하나가 그 도착률에 도착한 요청의
100%**이고 넷으로 나뉜다.

| 띠 | 무엇 |
|---|---|
| Met its rule | 받아들여져서 자기 규칙 안에 끝났다. **이 띠가 곧 모든 도착 분모의 달성률**이라 같은 채점의 다른 그림과 맞아떨어진다 |
| Admitted, missed | 받아들여져서 끝났는데 규칙 밖이다. **엔진 시간을 쓰고 위반을 배달한 요청**이고, 이 그림이 말하려는 띠다 |
| Unfinished at window close | 부하 창이 닫힐 때까지 안 끝났다. 결과를 모르므로 논문의 다른 모든 곳에서 `attain()`이 **두 분모 모두에서 뺀다** |
| Rejected | admission control이 엔진 시간을 쓰기 전에 거절했다 |

**색 (2026-09-02 변경)**: 이 저장소가 쓰는 색을 **채도만 55%로 낮춘 것** — 지킴 `#4b8a4b`
(`#2ca02c`에서), 받아서 어김 `#d18a4c`(`#ff7f0e`에서), 거절 `#c7c7c7`, 미완은 빗금 친 흰색.
흰색이 아니라 그 색 자신의 회색 쪽으로 섞으므로 밝기는 그대로다. `pace_and_outcome_35.pdf`가
같은 네 색으로 같은 네 갈래를 그린다 — 한 색이 논문 전체에서 한 결과를 뜻한다.

⚠ **미완(unfinished)이 여기서는 띠이고 `three_way_split.pdf`에서는 아니다.** 그 그림은
"결과를 아는 도착"을 분모로 세 몫을 말하고 빠진 몫을 캡션에 싣는데, **도착률 sweep에서는 그
선택이 무너진다** — 거절하거나 보류하는 네 정책에서 미완은 0.7~2.4%인데, 아무것도 거절하지 않고
큐를 쌓는 vLLM router에서는 **평균 19.7%, 최대 61.6%**다. 빼면 그 arm의 막대가 두 배로
다시 스케일되어 **가장 나쁜 자리에서 그 arm을 좋게 보이게 만든다.** 그래서 여기서는 아무것도
빼지 않는다.

**클라이언트 오류는 `missed`에 접어 넣었다.** 거절되지 않은 채 오류로 끝난 요청은 엔진 시간을
쓰고 위반이 된 것이므로 그 띠의 뜻과 같다. 여기 그려진 모든 run에서 최대 0.01%라 보이지 않는
띠를 따로 만들지 않았고, 값은 CSV의 `errored_pct` 열에 있다.

### 값이 어떻게 만들어지고, 무엇이 그것을 검사하나

`met`는 토큰 단위 채점 표(`results/aggregate_analysis/token_deadline_2026-08-31/`)에서 오고,
`rejected`·`unfinished`·오류는 그 run 자신의 `metrics.csv` 결과 플래그에서 온다. **서로 다른 두
곳에서 오므로 넷의 합이 100이라는 것이 실제 검사가 된다** — 79 run 전부에서 오차 1e-6 안으로
합이 100이고, 그렇지 않으면 스크립트가 종료한다. `missed`가 음수가 되는 경우(=두 곳이 같은
요청을 다르게 판정한 경우)도 종료 조건이다.

### 채점 규칙

`motivation_throughput_vs_goodput_4panel_t75.pdf`와 **같은 규칙**이다: `a_1 <= T` 이면서
`|{i : a_i <= T + i*P}| / N >= 0.95`(i 는 0부터). 클래스 예산 chat (5 s, 50 ms) · deepresearch
(10 s, 100 ms) · **swe (7 s, 75 ms)**. **swe를 전체 시간 30초로 채점한 어떤 수치와도 같은 표에
넣지 않는다.**

### 데이터

EXP-108(2026-08-31)의 네 arm(8 도착률 × 2반복) + **vLLM router는 EXP-77(2026-08-10)**.
vLLM router는 latency 약속을 읽지 않는 유일한 arm이라 자기가 돌지 않은 약속으로 채점하는 것이
정당하지만, **세션이 다르고 그 사이 2026-08-28에 장비 초기화·복원이 있었다.** 70 req/s 칸만
반복 하나다(나머지 39칸 전부 2).

### 캡션이 반드시 적어야 하는 것

- **막대는 도착 전체의 100%이고 미완이 그 안에 있다.** 논문의 달성률 수치는 미완을 분모에서
  빼므로, 이 그림의 초록 띠와 그 수치는 **vLLM router에서만 크게 다르다**(55 req/s에서
  초록 2.1%인데 미완을 뺀 달성률은 4.3%다).
- **swe가 per-token 형태(첫 토큰 7초 + 토큰당 75 ms)로 채점된다.**
- **llm-d의 10 req/s 칸은 두 반복이 갈린다**(거절 0.0% 대 42.0%). 막대는 평균이므로 그 칸의
  회색 띠는 어느 한 반복에도 없는 높이다. CSV의 `rejected_pct_min`/`_max`가 그것이다.
- **vLLM router만 다른 세션이다**(EXP-77, 2026-08-10).
- 55 req/s에서 읽히는 것: FluidServe는 초록 51.6% / 주황
  0.1%, llm-d는 23.6% /
  0.4%로 **둘 다 주황을 거의 없앴고**, PolyServe는
  24.4% / 33.1%,
  Llumnix SLO는 1.1% / 18.0%로
  **거절을 하면서도 주황이 남는다** — 거절이 못 지킬 일을 자르고 있지 않다는 뜻이다.

**다시 만들려면**: `python3 paper_figures/fig_outcome_split_sweep.py`

## `decode_iteration_cdf.pdf` — chat가 올라간 엔진의 decode iteration 시간, 기준선 셋

**스크립트**: `fig_decode_iteration_cdf.py`. **크기**: 7.0 × 1.95 in, `figure*`에
`width=\textwidth`(스케일 1.0). **값**: 같은 basename의 `decode_iteration_cdf.csv`
(2,400행 = 곡선 24개 × 격자 100점, 각 행이 `panel, arm, run, engine, role, n_windows, itl_ms, cdf`).

패널 둘: **(a) 한 시간 동적 trace, (b) 정적 sweep 35 req/s.** 각 패널에 기준선 셋의 CDF와
chat의 토큰당 예산 **50 ms** 수직선.

### 무엇을 주장하는 그림인가

세 기준선이 **"다음 요청이 들어갈 자리가 있는지를 얼마나 보수적으로 판정하는가"**라는 한 축의
다른 지점에 있고, 그 판정이 엔진의 decode iteration 시간으로 직접 드러난다.

| arm | 한 시간 trace | 35 req/s |
|---|---|---|
| llm-d | 중앙값 36.5 ms, **72.5%가 예산 안** | 중앙값 37.3 ms, **70.1%가 예산 안** |
| Llumnix SLO | 중앙값 53.4 ms, 46.0% | 중앙값 74.4 ms, 32.0% |
| PolyServe (chat tier) | 중앙값 **114.2 ms**, 12.3% | 중앙값 63.3 ms, 16.7% |

배치 크기가 그 기제다 — 35 req/s에서 running batch 중앙값이 llm-d 96, Llumnix SLO 148,
PolyServe(chat tier) 405이다.

⚠ **평균이 아니라 중앙값으로 말해야 한다.** 세 arm 모두 오른쪽 꼬리가 길어서 **평균 iteration
시간은 셋 다 50 ms를 넘는다**(35 req/s에서 llm-d 58.5 / Llumnix SLO 124.3 / PolyServe 74.1 ms).
이 그림이 뒷받침하는 주장은 **질량이 어디 있는가**이지 평균이 아니다. "llm-d의 평균 decode
시간이 50 ms보다 훨씬 작다"는 문장은 이 데이터에서 **거짓**이다. 막대가 아니라 CDF를 그린
이유가 그것이다.

⚠ **PolyServe는 항상 둘 사이에 있지 않고, 두 패널이 서로 다른 것을 말한다.** 35 req/s에서는
둘 사이(63.3 ms)인데 한 시간 trace에서는 **셋 중 가장 나쁘다**(114.2 ms = 예산의 2.3배,
예산 안 12.4%). 파티션이 그 tier에 한 시간 99,240 도착 중 **57,724건**을 몰아주는 동안 dr tier
엔진은 중앙값 31.0 ms로 돌았기 때문이다. **정적 파티션은 이 축 위에 있지 않고, 어느 쪽에
떨어지는지는 설정된 클래스 믹스가 정한다** — "중간"이라고 쓰면 안 된다.

### 그리는 양, 그리고 그것이 이름값을 하는지에 대한 검사

한 점 = 한 엔진의 1초 스크레이프 창 하나이고, 값은
`Δ(vllm:inter_token_latency_seconds_sum) / Δ(..._count)` — **엔진 자신이 기록한** 그 창의
연속한 두 출력 토큰 사이 평균 시간(ms)이다. 연속 배칭에서는 running 요청 하나하나가 decode
step마다 토큰 하나를 내므로 이것이 곧 iteration 시간인데, **그 사이에 prefill step이 끼면
달라진다.** 그래서 지연 histogram을 전혀 쓰지 않는 독립 유도
`running_batch × Δt / Δ(generation_tokens)`와 대조했고 일치한다 — 35 req/s 세 arm의 중앙값이
37.3 대 39.1, 74.4 대 73.1, 63.3 대 62.9 ms이고 창 단위 상관이 0.93~0.96이다. **같은 이름의 두
양이 실제로 같은 양이라는 것을 확인한 것이고, 이 저장소는 그렇지 않았던 경우를 두 번 겪었다.**

### 어느 엔진을 그렸고 왜 그 엔진인가

엔진의 허용 속도는 **그 위 요청들의 토큰당 예산 중 최솟값**으로 정해지므로, 봐야 하는 것은
세 클래스 중 가장 빡빡한 chat(50 ms)이 올라간 엔진이다. 클라이언트 request id와 스케줄러
dispatch 로그를 조인해 엔진별 chat 비중을 냈다:

| 패널 | arm | 엔진별 chat 비중 | 그린 엔진 |
|---|---|---|---|
| (a) 한 시간 | PolyServe | 8001이 **100.0%**(chat tier), 8000 dr, 8002 swe, 8003 65.2% | **8001** |
| (a) 한 시간 | Llumnix SLO | 네 엔진 65.3~69.9% | 8003 (69.9%) |
| (b) 35 req/s | PolyServe | 8003 **98.8%**, 8000 97.5%, 8001 swe, 8002 dr | **8003** |
| (b) 35 req/s | Llumnix SLO | 8000 72.0 / 8003 73.4 / 8001 43.6 / 8002 33.1% | 8003 (73.4%) |

⚠ **llm-d는 이 방법으로 귀속되지 않고, 하지 않았다.** 자기 inference gateway로 라우팅해서
우리 스케줄러의 dispatch 로그에 거의 남지 않는다. 그 자리를 대신하는 것이 **음영 띠**다 —
모든 arm에 대해 네 엔진 곡선의 포락선이고, **llm-d의 네 엔진은 중앙값이 3.8 ms 안에 모인다**
(35 req/s에서 37.3 / 37.4 / 38.9 / 41.1). 어느 것을 그리든 주장이 바뀌지 않는다는 뜻이다.
**두 실선의 차이를 읽기 전에 띠를 먼저 읽는다.**

### 데이터

| 패널 | 실험 | run |
|---|---|---|
| (a) | EXP-71b, 한 시간 동적 trace (2026-08-08/09), arm당 run 하나 | `260809_0210_exp71br1_polyserve_fullb` · `260809_0057_exp71br1_slo_fullb` · `260808_2007_exp71br1_llmdslo_full` |
| (b) | EXP-108 (2026-08-31) 35 req/s, 두 반복 중 **반복 1** | `260831_0805_exp108r1_polyservept75_*` · `260831_0956_exp108r1_slot75_*` · `260831_0548_exp108r1_llmdslot75_*` |

**두 패널 모두 아무것도 채점하지 않는다.** 그래서 두 실험 사이에 달라진 swe의 약속은 여기 들어
오지 않는다 — 그려지는 유일한 예산은 chat의 토큰당 50 ms이고, 그 값은 이 저장소가 쓴 모든
워크로드 설정에서 동일하다.

### 캡션이 반드시 적어야 하는 것

- **중앙값과 질량으로 말한다. 평균 iteration 시간은 세 arm 모두 50 ms를 넘는다.**
- **PolyServe는 "중간"이 아니다.** 한 시간 trace에서는 셋 중 가장 나쁘고, 그 이유는 파티션이
  그 tier에 준 트래픽 양이다.
- **llm-d는 엔진별 클래스 귀속이 불가능하다.** 그 자리를 대신하는 것은 네 엔진의 중앙값이
  37.3 / 37.4 / 38.9 / 41.1 ms로 3.8 ms 안에 모인다는 사실이고, **음영을 없앤 뒤로 그림에 없으니
  캡션이 그 수치를 적어야 한다.**
- **네 갈래 띠의 색은 이 저장소가 쓰는 색을 채도만 낮춘 것이다 (2026-09-02 사용자 지시)** —
  지킴은 `#2ca02c`를 채도 55%로 낮춘 `#4b8a4b`, 받아서 어김은 `#ff7f0e`를 같은 비율로 낮춘
  `#d18a4c`, 거절은 `#c7c7c7`, 미완은 빗금 친 흰색. 흰색이 아니라 **그 색 자신의 회색 쪽으로**
  섞는다 — 흰색으로 섞으면 밝아져서 네 띠가 사이의 흰 빗금 띠와 분리되지 않는다.
  `outcome_split_sweep_t75.pdf`가 같은 네 색을 쓴다 — 한 색이 논문 전체에서 한 결과를 뜻한다.
- **x축이 200 ms에서 잘리고 Llumnix SLO의 곡선은 0.81에서 멈춘다.** 그 arm의 창 18.99%가 그
  위에 있다(다른 셋은 1.8~4.4%).
- **4패널 그림에는 오차 띠가 없다.** 반복 폭은 그 그림의 CSV의 `*_min` / `*_max` 열에 있고,
  칸마다 반복 수는 `n_repeats`다.
- **x축이 600 ms에서 잘린다.** 잘리는 몫은 한 시간 PolyServe chat tier에서 0.35%(최대
  4,519 ms)이고 나머지 23개 곡선 전부에서 0.05% 이하다.
- 각 패널은 arm당 run 하나이므로 **반복 간 편차가 그려져 있지 않다.** 띠는 반복이 아니라
  같은 run 안 네 엔진의 폭이다.

**다시 만들려면**: `python3 paper_figures/fig_decode_iteration_cdf.py`

## `pace_and_outcome_35.pdf` / `_withfs.pdf` — 한 축의 양 끝과 그 대가, 35 req/s

**스크립트**: `fig_pace_and_outcome_35.py`. 플래그 둘로 판 넷을 쓴다 — `--with-ours`가
FluidServe를 넣고, `--full`이 두 칼럼 폭으로 다시 그린다.

| 파일 | 크기 | 넣는 곳 |
|---|---|---|
| `pace_and_outcome_35.pdf` | **3.335 × 2.20 in** | `figure`에 `width=\columnwidth` |
| `pace_and_outcome_35_withfs.pdf` | 3.335 × 2.20 in | 같음 |
| `pace_and_outcome_35_full.pdf` | 7.0 × 1.75 in | `figure*`에 `width=\textwidth` |
| `pace_and_outcome_35_withfs_full.pdf` | 7.0 × 1.75 in | 같음 |

**값**: 같은 basename의 `.csv` — (a)의 곡선(격자 100점 × 엔진 넷 × arm), (a)의 평균선
(`panel=a_mean`), (b)의 막대(`panel=b`, 네 갈래 컬럼). arm 집합이 다르면 CSV도 다르다.

⚠ **두 폭은 같은 그림의 두 판이지 한 판을 확대·축소한 것이 아니다.** 3.335 in은
`(\textwidth − \columnsep)/2`이고 `intro_reject_throughput.pdf`와 같은 폭이라
`\includegraphics`가 배율 1.0으로 넣는다(높이만 1.75 → 2.00 in인데, 패널마다 키가 하나씩 위에
붙기 때문이다). **어느 파일도 다른 폭으로 스케일하지 않는다.**

**폰트 (2026-09-02 사용자 지시).** 축 이름과 숫자 눈금 라벨과 패널 이름은 **양쪽 폭 모두
프로젝트 스타일의 8 pt 그대로**이고, 그것이 이 그림을
`motivation_throughput_vs_goodput_4panel_t75_withfs.pdf` 옆에 놓았을 때 글자 크기가 달라지지
않게 하는 것이다. 한 칼럼에서 줄인 것은 **글자 크기가 아니라 글자 수**다 — (a)의 축 이름이
"Decode iteration time (ms)"에서 "Iteration time (ms)"로, y축 이름이 "CDF over 1 s windows"에서
"CDF"로, "Share of arrivals (%)"에서 "Arrivals (%)"로 준다. **8 pt가 아닌 것은 둘뿐이다**:
두 범례(6.2 pt)와 패널 (b)의 arm 이름(6.0 pt, 30° 회전). 정책 이름 셋을 8 pt로 한 줄에 놓으면
약 1.85 in이라 1.35 in 패널을 넘고, arm 이름 셋은 1.15 in 패널 아래에서 읽을 수 있는 어떤
크기로도 가로로 부딪힌다. arm 이름은 4패널 그림에 대응하는 요소가 없다.

**범례는 그래프 위에 둘, 각각 두 줄이다 (2026-09-02 사용자 지시).** 왼쪽 위에는 정책 이름과
`arm's mean`, 오른쪽 위에는 네 갈래 띠. 한 줄로 놓으면 5 pt까지 내려가야 해서 두 줄로 바꿨고,
그 대가가 캔버스 높이 0.12 in이다. **키는 캔버스의 두 모서리에 붙인다** — 둘이 합쳐 약 2.7 in인데
패널 둘이 차지하는 폭이 2.8 in이라, 각자 자기 패널 위 가운데에 놓으면 가운데에서 서로 겹친다.
50 ms 예산선만 키가 아니라 주석인데(다섯째 키 항목이 세 번째 줄을 만든다), 파선 왼쪽에 "50 ms"로
붙인다 — 오른쪽에 두면 세 평균 점선 사이에 놓여 취소선처럼 읽힌다.

⚠ **점선 세로선 셋은 그 arm의 평균 iteration 시간이고, 이제 범례에 이름이 있다.** 처음 그렸을 때는
이름이 없어서 그림을 처음 본 사람이 "저 점선들은 무엇이냐"고 물었다 — 그림에 이름 없는 선을 두면
캡션을 읽기 전에는 알 수 없고, 캡션은 그림보다 늦게 읽힌다.

⚠ **음영을 없앴다 (2026-09-02 사용자 지시).** 예전에는 arm마다 네 엔진 곡선의 포락선을 음영으로
깔았는데, 1.35 in 패널에서 곡선 여섯 뒤에 색면 넷이 깔리면 정보가 아니라 얼룩으로 읽힌다.
**그 정보는 사라지지 않고 CSV의 `panel = a_spread` 행으로 남는다**(그리지 않은 엔진마다 중앙값
한 줄). llm-d의 엔진별 클래스 귀속을 대신하던 것이 이 음영이었으므로, **이제 그 몫은 캡션이
져야 한다** — 아래 캡션 의무를 본다.

| 패널 | 이름 | 무엇 |
|---|---|---|
| (a) | **Decode time** | 가장 빡빡한 토큰당 예산(chat 50 ms)을 든 엔진의 **decode iteration 시간 CDF**. 검은 파선 = 예산, **arm 색 점선 = 그 arm의 평균**(범례에 `arm's mean`으로 이름이 있다) |
| (b) | **Arrival outcome** | **같은 조건에서 도착한 모든 요청**이 어떻게 됐나 — 지킴 / 받아서 어김 / 미완 / 거절 |

패널 이름은 **각 그래프 아래**에 있고, 한 칼럼판에서는 축에 붙이지 않고 캔버스 좌표의 한 높이에
같이 놓는다 — (b)의 눈금 라벨이 회전해서 (a)보다 키가 크므로 축에 붙이면 두 이름이 다른 높이에
찍혀 오식처럼 보인다.

⚠ **x축이 200 ms에서 끝난다 (2026-09-02 사용자 지시).** 그 위로 잘리는 창의 비율은 PolyServe
2.78%, llm-d 4.38%, FluidServe 1.82%, **Llumnix SLO 18.99%**(최대 564 ms)다. 그래서 **Llumnix
SLO의 곡선만 오른쪽 끝에서 1.0에 닿지 않고 0.81에서 멈춘다** — 잘린 것이지 데이터가 없는 것이
아니고, 곡선이 천장에 닿지 않는 것 자체가 그 arm의 꼬리다. **캡션이 이 수치를 적어야 한다.**

### 주장

컨트롤플레인은 "하나 더 들어갈 자리가 있는가"를 판정하고 **두 방향으로 틀릴 수 있다.**
(a)가 그 판정을 엔진에서 본 것이고 (b)가 그 판정의 대가다.

| arm | (a) 중앙값 | 예산 안 비율 | 평균 | (b) 지킴 | 받아서 어김 | 거절 |
|---|---|---|---|---|---|---|
| llm-d | 37.3 ms | **70.3%** | 58.5 ms | 43.6% | 0.7% | 54.6% |
| PolyServe | 63.3 ms | **16.7%** | 74.1 ms | 45.3% | 41.5% | 10.5% |
| Llumnix SLO | 74.4 ms | **32.2%** | 124.3 ms | 20.2% | 36.7% | 39.2% |

**llm-d는 보수적으로 판정한다** — iteration의 70.3%가 예산 안이고 중앙값이 37.3 ms라 함대를
채우지 않는다. 그 대가와 이득이 (b)에 같이 있다: 받아들인 것은 거의 안 어기는데(도착의 0.7%)
**도착의 54.6%를 거절**해서 그렇다.
**Llumnix SLO는 공격적으로 판정한다** — 중앙값 74.4 ms로 예산의 1.5배이고 예산 안이 32.2%뿐이다.
(b)에서 **39.2%를 거절하고도 36.7%를 받아서 어긴다** — 거절이 못 지킬 일을 자르고 있지 않다.
**PolyServe는 요청마다 판정하지 않고 클래스로 나눈다** — chat tier 엔진이 중앙값 63.3 ms이고
거절이 10.5%뿐이라 도착의 41.5%가 받아들여진 뒤 어긴다.

⚠ **평균선을 (a)에 그린 이유는 평균이 이 주장을 뒤집기 때문이고, 그림이 그것을 보여야 한다.**
세 arm 모두 오른쪽 꼬리가 길어서 **평균 iteration 시간은 셋 다 예산을 넘는다**(58.5 / 124.3 /
74.1 ms) — iteration의 대부분이 예산 안인 arm도 그렇다. **"llm-d의 평균 decode 시간이 50 ms보다
훨씬 작다"는 문장은 이 데이터에서 거짓이다.** 참인 것은 질량이 어디 있는가이고, 그것이 CDF와
평균선을 같이 그린 이유다.

⚠ **(b)에 넷째 띠가 있다.** 창이 닫힐 때까지 안 끝난 요청은 결과가 없어서 논문의 다른 곳에서
`attain()`이 두 분모 모두에서 빼므로, 여기서 "지킴"이나 "어김" 어느 쪽에도 접어 넣을 수 없다.
이 셋에서 1.1~3.9%다. **막대는 도착 전체의 100%다.**

### 두 패널의 범위가 다르다

(a)는 **run 하나의 엔진 하나**(chat을 든 엔진, 반복 1)다 — iteration 시간 분포는 엔진의 성질이기
때문이다. (b)는 **두 반복 전부의 모든 요청**이다. **(a)에는 반복 폭이 그려져 있지 않고**, 같은
run 안 다른 엔진들의 중앙값은 CSV의 `panel = a_spread`에, (b)의 반복 폭은
`outcome_split_sweep_t75.csv`에 있다.

### 어느 엔진을 그렸나

엔진의 허용 속도는 그 위 요청들의 토큰당 예산 중 최솟값이므로 chat이 올라간 엔진을 본다.
클라이언트 request id와 스케줄러 dispatch 로그를 조인한 결과: **PolyServe 8003이 chat 98.8%**
(그 arm의 chat tier), **Llumnix SLO 8003이 73.4%**(네 엔진 중 최고). ⚠ **llm-d는 자기 inference
gateway로 라우팅해서 이 방법으로 귀속되지 않는다** — 그 자리를 대신하는 것은 **llm-d의 네 엔진이
서로 같게 움직인다는 사실**이고, 중앙값이 37.3 / 37.4 / 38.9 / 41.1 ms로 3.8 ms 안에 모인다.
음영을 없앴으므로 이 수치는 그림에 없다. **캡션이 적어야 한다.**

### `--with-ours` 판이 보여주는 것

FluidServe는 중앙값 43.3 ms, 예산 안 **69.2%**로 llm-d(70.3%)와 **거의 같은 몫이 예산 안**인데,
꼬리가 짧고(p90 70.6 대 97.3 ms) 평균이 51.4 ms로 예산에 붙는다. 그리고 (b)에서 지킴이
**75.5%**로 llm-d의 43.6%의 1.7배다 —
거절이 22.2% 대 54.6%이기 때문이다.
**같은 크기의 예산 안 몫을 두 시스템이 전혀 다른 양의 완료로 바꾼다**는 것이 이 쌍의 요점이다.

### 데이터

EXP-108(2026-08-31) 35 req/s, 2반복. (a)는 반복 1의 run
(`260831_0805_exp108r1_polyservept75_*`, `260831_0956_exp108r1_slot75_*`,
`260831_0548_exp108r1_llmdslot75_*`, `--with-ours`는 `260831_0257_exp108r1_fsv3capgnofrct75_*`).
(b)의 채점은 고정된 사다리 규칙(관용 95%, goodput 토큰 단위), 클래스 예산 chat (5 s, 50 ms) · deepresearch
(10 s, 100 ms) · **swe (7 s, 75 ms)**.

### 캡션이 반드시 적어야 하는 것

- **중앙값과 질량으로 말한다. 평균 iteration 시간은 세 arm 모두 50 ms를 넘고, 그래서 평균선이
  그림에 있다.**
- **(b)의 막대는 도착 전체의 100%이고 미완이 그 안에 있다.**
- **llm-d는 엔진별 클래스 귀속이 불가능하다.** 그 자리를 대신하는 것은 네 엔진의 중앙값이
  37.3 / 37.4 / 38.9 / 41.1 ms로 3.8 ms 안에 모인다는 사실이고, **음영을 없앤 뒤로 그림에 없으니
  캡션이 그 수치를 적어야 한다.**
- **네 갈래 띠의 색은 이 저장소가 쓰는 색을 채도만 낮춘 것이다 (2026-09-02 사용자 지시)** —
  지킴은 `#2ca02c`를 채도 55%로 낮춘 `#4b8a4b`, 받아서 어김은 `#ff7f0e`를 같은 비율로 낮춘
  `#d18a4c`, 거절은 `#c7c7c7`, 미완은 빗금 친 흰색. 흰색이 아니라 **그 색 자신의 회색 쪽으로**
  섞는다 — 흰색으로 섞으면 밝아져서 네 띠가 사이의 흰 빗금 띠와 분리되지 않는다.
  `outcome_split_sweep_t75.pdf`가 같은 네 색을 쓴다 — 한 색이 논문 전체에서 한 결과를 뜻한다.
- **x축이 200 ms에서 잘리고 Llumnix SLO의 곡선은 0.81에서 멈춘다.** 그 arm의 창 18.99%가 그
  위에 있다(다른 셋은 1.8~4.4%).
- **4패널 그림에는 오차 띠가 없다.** 반복 폭은 그 그림의 CSV의 `*_min` / `*_max` 열에 있고,
  칸마다 반복 수는 `n_repeats`다.
- **(a)는 run 하나·엔진 하나, (b)는 두 반복 전부**이며 (a)의 띠는 반복 폭이 아니다.
- **기본 판에 FluidServe는 일부러 없다.** 두 실패 방식이 한 축의 양 끝이라는 것을 세우는
  그림이고, 우리 시스템은 그 축 위에 그 다음에 놓인다.
- **한 칼럼 판에서는 캡션이 점선을 설명해야 한다** — arm 색 점선은 그 arm의 평균 iteration
  시간이다. 두 칼럼 판은 그것을 축 이름에 싣고 있어 이 문장이 필요 없다.

**다시 만들려면**: `python3 paper_figures/fig_pace_and_outcome_35.py [--with-ours]`

## `exp109_hour.pdf` / `_4panel.pdf` / `_five.pdf` — 한 시간 mix-shift trace, swe를 per-token으로 약속한 판

**스크립트**: `fig_exp109_hour.py`(`--arms five`가 다섯 arm 판을 쓴다).
**크기**: 3패널 7.0 × 1.70 in, 4패널 7.0 × 1.75 in, `figure*`에 `width=\textwidth`.
**값**: 같은 basename의 `.csv` — 창마다 한 행(`arm, minute, throughput_tok_s,
goodput_tok_s, attainment_admitted_pct, attainment_offered_pct, in_flight_at_end_frac,
cut_at_minute`).

**`exp71_hour.pdf`를 대체하지만 그것을 다시 그린 것이 아니다.** 셋이 다르고 각각 하나만으로도
두 그림의 수치를 나란히 놓을 수 없다:

| | `exp71_hour.pdf` | 이 그림 |
|---|---|---|
| trace | `dyn60_short_m123_b1045` (믹스 m1→m2→m3→m1) | **`dyn60_shift_m2Am1B_b1045`** (믹스 m2→A→m1→B, chat이 93.0 → **33.3** → 76.9 → 60.0%) |
| swe 약속 | 전체 시간 30초 | **첫 토큰 7초 + 토큰당 75 ms** |
| FluidServe | v0.2 | **v0.4** (바이너리 `6dc9f035`) |

도착 시각은 두 trace가 바이트 동일하고 대역도 10.8~45.0 req/s로 같다. 다른 것은 클래스 믹스뿐이고,
**가운데 구간이 균등 믹스**라 요청당 일감이 첫 구간의 약 네 배가 된다 — 옛 trace는 그 구간에
가지 않는다.

### arm 집합이 둘인 이유 — 그리고 vLLM router를 빼지 않는 방법

| 파일 | arm | 곡선이 끝나는 분 | 달성률 분모 | 패널 순서 |
|---|---|---|---|---|
| `exp109_hour.pdf` / `_4panel.pdf` | 거절·보류하는 넷 | 59.2 | admitted, offered | goodput → admitted → offered (4패널은 앞에 throughput) |
| `exp109_hour_five.pdf` | **다섯 전부** | 57.8 | **모든 도착** | **goodput → SLO → throughput** |
| `exp109_hour_five_admitted.pdf` | 다섯 전부 | 59.2 | admitted | goodput → SLO → throughput |

**x축은 세 판 모두 0~60분이고 눈금은 0·15·30·45·60이다 (2026-09-03 지시).** 곡선이 끝나는 분은
판마다 다른데(59.2 / 57.8 / 59.2) 그 이유가 서로 무관하고 — warmup·drain 트림, 창을 중심에 찍는
것, 미완 규칙 — 각각을 자기 축 끝으로 삼으면 **같은 한 시간의 그림 셋이 x축 길이가 셋 다 달라져
서로 다른 run처럼 읽힌다.** 오른쪽 끝의 짧은 여백이 그 트림이고, 그 크기는 최대 2.2분이다.

**선 굵기는 0.8 pt다 (2026-09-03 지시, 종전 1.1).** 다섯 계열이 각각 약 110점이고 자주 교차하는데,
굵으면 교차가 하나의 띠로 뭉친다.

⚠ **패널 순서는 2026-09-03 지시로 goodput → 요청 단위 달성률 → throughput이다.** goodput이 이
그림이 주장하는 양이고, 그 다음이 같은 질문의 요청 단위 판이며, throughput은 그 동안 엔진이
무엇을 하고 있었는가다.

⚠ **처음에는 다섯 arm 판을 25.2분에서 잘랐는데 그럴 필요가 없었고, 그 판단이 틀렸다
(2026-09-03 사용자 지적으로 고침).** 사정은 이렇다. 창이 닫힐 때 미완인 요청은 `attain()`이
**두 분모 모두에서 빼므로**, 백로그가 쌓인 arm의 마지막 창은 끝난 요청(빠른 쪽)만 남아 너무
높게 읽힌다. 그 기준(창의 미완 비율 20%)으로 재면 거절·보류하는 넷은 60분까지 안 넘고
vLLM router는 26분에 넘어 58분부터 100%라, 다섯을 한 그림에 놓으려면 26분에서 잘라야 했다.

**그런데 그 결함은 분모가 미완을 빼기 때문에 생기는 것이고, 이 저장소에는 빼지 않는 분모가
이미 정의돼 있다** — `all_arrivals_attainment.py`의 셋째 열
`met / (met + missed + rejected + unfinished)`. 창이 **도착 시각** 기준이므로(`windows()`),
10분에 도착해서 끝내 안 끝난 요청은 10분 창에 속하고 **50분 넘게 걸린 것**이다. 그것을
"결과를 모른다"고 빼는 것이 인공물을 만든 것이고, 위반으로 세면 인공물이 없어진다.
**그래서 다섯 arm 판은 자르지 않는다** — 유일한 트림은 trace 자체의 끝 2분인데, 마지막
몇 분에 도착한 요청은 어떤 정책이든 run 안에 못 끝내므로 **모든 arm이 똑같이** 영향을 받는다.

**대가**: 그 판의 달성률은 admitted가 아니라 모든 도착 분모다.

### ⚠ 20% 규칙은 대리 지표이고, 그 대리가 틀리는 자리가 있다 (2026-09-03 수정)

**처음에는 admitted 판의 다섯 arm을 25.2분에서 잘랐는데 그럴 필요가 없었다.** 20% 규칙이
막으려는 것은 하나다 — 백로그가 쌓인 arm의 마지막 창에 **끝난 요청만 남고 그것들이 규칙을
지킨 것들이면** admitted가 부풀려진다. vLLM router는 26분에 그 문턱을 넘지만, **부풀려지지
않는다**: 그 arm은 끝난 요청도 규칙을 못 지키므로 admitted가 0.0%이고, 아예 아무것도 안 끝난
창에서는 **정의되지 않는다**(NaN이라 선이 끊긴다).

실측: 그려지는 축 전체에서 **vLLM router의 admitted가 모든 도착 값을 넘는 최대치가 0.00점**이고,
118창 중 21창에서 admitted가 정의되지 않는다. 그래서 이 판은 **넷 arm 판과 같은 59.2분 축**을
쓰고, 축을 정하는 20% 계산은 admission control이 있는 네 arm에 대해서만 한다.

**스크립트가 매 실행마다 그 팽창을 재서 찍는다** — 대리 지표를 믿는 대신 대리가 서 있는 양을
직접 재고, 실제로 팽창이 있으면 그때 축을 줄인다.

`exp109_hour_five_admitted.pdf`가 그 판이다. ⚠ **그래도 축의 마지막 2분은 모든 판에서 잘 하고
있는 arm을 부풀린다** — 마지막 다섯 창에서 admitted가 모든 도착 분모보다 얼마나 높은지:

| arm | admitted | 모든 도착 | 차이 |
|---|---|---|---|
| **llm-d** | 96.4 | 28.4 | **+68.0** |
| **FluidServe** | 98.5 | 70.0 | **+28.5** |
| PolyServe | 29.7 | 23.5 | +6.2 |
| Llumnix SLO | 0.0 | 0.0 | 0.0 |
| vLLM router | (전부 NaN) | 0.0 | — |

**팽창은 vLLM router가 아니라 FluidServe와 llm-d에서 일어난다.** trace가 끝나는 순간 아직
돌고 있던 요청이 admitted 분모에서 빠지고 남는 것은 이미 끝난 빠른 요청들인데, 그것들은 규칙을
지킨 것들이다. **이것은 넷 arm 판에도 그대로 있다** — 20% 규칙이 지우지 못하는 잔여이고,
**캡션이 마지막 2분을 그렇게 읽지 말라고 적어야 한다.** 그 구간을 빼고 읽고 싶으면 모든 도착
분모 판(`exp109_hour_five.pdf`)을 본다.

### 곡선이 왜 60분까지 안 가나

trace는 약 61분치 도착을 담고 있고, 곡선이 59.2분에서 끝나는 것은 셋이 겹친 결과다:

| 단계 | 끝나는 분 |
|---|---|
| 원본 도착 구간 | 61.01 |
| `load_run`이 앞 60초(warmup)와 뒤 20초(drain)를 자른다 | 60.67 |
| 창이 90초이고 **창의 중심**에 점을 찍는다 | 59.92 |
| 미완 20% 규칙(Llumnix SLO가 그 뒤로 넘는다) | **59.2** |

drain 20초는 "그만큼 늦게 도착한 요청은 어떤 정책이든 run 안에 못 끝낸다"이고, 창 중심 규칙은
마지막 창이 45초 앞에 찍힌다는 뜻이다. **다섯 arm 모든 도착 판이 57.8분인 것은 넷째 줄 대신
trace 끝 2분을 뺐기 때문이다.** 축 자체는 세 판 모두 60분까지 그린다.

### 한 시간 전체 수치 (반복 2, 이 그림이 그리는 run)

| arm | 거절률 | offered | admitted | 미완 최대 |
|---|---|---|---|---|
| **FluidServe v0.4** | 20.7% | **79.3** | **100.0** | 7.5% |
| llm-d | 42.5% | 56.3 | 98.0 | 5.4% |
| PolyServe | 17.9% | 53.6 | 65.2 | 5.5% |
| vLLM router | **0.0%** | 45.4 | 45.4 | **100.0%** |
| Llumnix SLO | 39.9% | 28.3 | 47.0 | 20.2% |

⚠ **고정 규칙이 2·3위를 바꾼다.** 기존 규칙(평균)에서는 llm-d(54.0) > PolyServe(30.8)였는데
사다리에서는 llm-d 56.3과 PolyServe 53.6이 겹친다. llm-d의 반복 간 편차가 6.4점이므로 **둘은
구별되지 않는다**(EXP-109 §5 ③). 1위와 그 격차는 두 규칙에서 같다.

⚠ **PolyServe가 이제 거절한다(17.8%).** `exp71_hour.pdf`의 설명문은 "PolyServe와 vLLM router는
admission control이 없다"고 적는데, EXP-106이 논문의 메커니즘 여섯을 되살린 뒤로 PolyServe에는
있다. **거절률 0%인 arm은 이제 vLLM router 하나뿐이고**, 그래서 그 arm만 두 attainment 패널이
같은 선이다.

### 데이터와 run 선택

EXP-109(2026-09-01/02), **반복 2**, arm당 run 하나. 대체하는 그림이 한 pass를 그렸으므로 같은
규약을 따랐고, 다른 반복과 두 반복의 한 시간 수치는 실험 파일에 있다.
run은 `/home/nxclab/tools/pick_usable_run.sh`가 고른다 — 병합됐고 처음 도착부터 마지막 완료까지
90분 이내인 것 중 **가장 최근**. ⚠ **이 선택은 장식이 아니다**:
`results/*exp109r1_vllmcachet75_shift`에 다섯 디렉토리가 걸리는데 하나는 끝나지 않은 293분짜리
이고 셋은 헤더만 남은 잔재이며, `ls | head -1`은 **가장 오래된 것**을 집는다.

**채점은 문서 맨 앞에 적은 고정 규칙이다**(사다리, 관용 95%, goodput 토큰 단위). 2026-09-03에
한 시간 run 열 개의 토큰별 도착 시각 파일을 다시 읽어 이 규칙으로 채점했고, 그래서 **이 그림의
수치는 정적 sweep 그림들의 수치와 같은 규칙 위에 있다.**

**교차검증**: 이 그림이 내는 한 시간 전체 값이 `experiments/EXP-109_per-token-swe-hour-trace.md`
§4의 누적 규칙 표(반복 2)와 **소수 첫째 자리까지 일치한다** — FluidServe 79.3 / 100.0,
llm-d 56.3 / 98.0, PolyServe 53.6 / 65.2, Llumnix SLO 28.3 / 47.0, vLLM router 45.4 / 45.4.
그 표는 그 실험이 독립으로 낸 것이고 이 그림은 요청 단위 판정을 창별로 다시 센 것이므로,
일치는 두 경로가 같은 규칙을 같은 데이터에 적용했다는 확인이다.

### ⚠ 패널 (a)는 클라이언트가 아니라 엔진이 잰 값이다 (2026-09-03 수정)

**같은 이름의 두 양이 있었다.** 원래 구현은 각 요청의 `output_tokens`를 그 요청이 **도착한**
창에 더했는데, 그것은 그 분에 엔진이 내고 있던 속도가 아니다 — 40분에 도착해서 20분 동안
생성하는 요청은 토큰 전부를 40분에 놓고, 끝내 안 끝난 요청은 잘린 개수를 거기 놓는다. 백로그가
작은 arm에서는 둘이 거의 같지만, **도착의 절반이 안 끝나는 arm에서는 완전히 다르다**: 그렇게
그리면 vLLM router가 20분경 20k tok/s로 치솟았다가 33분에 0으로 떨어지는 곡선이 나오는데,
**무너진 것은 생산이 아니라 귀속이다.**

지금은 엔진 자신의 단조 카운터 `vllm:generation_tokens_total`의 스크레이프 간 차분을 네 엔진에
대해 더한다. 그 값으로 보면 vLLM router는 **0으로 가지 않고 20분 이후 3~6k tok/s로 돌아간다** —
엔진은 계속 토큰을 만들고 있고 그중 규칙 안에 들어가는 것이 거의 없다((b)에서 15분 이후 0 부근).
**"admission control이 없으면 함대가 쓸모없는 일로 바쁘다"가 이 세 패널의 요점이고, 앞의 구현은
그것을 "함대가 멈춘다"로 잘못 말했다.**

### 캡션이 반드시 적어야 하는 것

- **세로 회색 점선은 15분마다 바뀌는 클래스 믹스의 경계다**(m2 → A → m1 → B). 그림에 이름이 없다.
- **(a)는 엔진이 보고한 생성 속도**이고 (b)·(c)·(d)는 클라이언트 기록이다.
- **x축은 0~60분이고 곡선이 끝나는 분은 판마다 다르다**: 넷 arm 59.2분, 다섯 arm(모든 도착)
  57.8분, 다섯 arm(admitted) 59.2분. 오른쪽 끝의 여백이 트림이며 최대 2.2분이다.
- **넷 arm 판과 admitted 판은 미완 요청이 두 분모에서 빠지기 때문에 잘렸다.** 다섯 arm의 모든
  도착 판은 미완을 위반으로 세므로 자르지 않았고(끝 2분만 트림), 그래서 그 판의 달성률은 다른
  판보다 낮게 나온다 — **분모가 다르다.**
- **거절률 0%인 arm은 vLLM router 하나이고**, 그래서 (c)와 (d)에서 그 arm만 두 선이 같다.
- **swe가 per-token 형태(첫 토큰 7초 + 토큰당 75 ms)로 약속·채점됐다.** 전체 시간 30초로 채점한
  어떤 수치와도 같은 표에 넣지 않는다.
- **arm당 run 하나**이므로 반복 폭이 그려져 있지 않다. 같은 arm의 세션 간 이동은 FluidServe에서
  offered 3.8점으로 측정돼 있다(EXP-109 §8).

**다시 만들려면**: `python3 paper_figures/fig_exp109_hour.py [--arms five]`

## `exp113_hour_qwen.pdf` / `_4panel.pdf` — Qwen2.5-72B에서 돌린 한 시간, FluidServe 하나

**스크립트**: `fig_exp113_hour_qwen.py`. **크기**: 3패널 7.0 × 1.70 in, 4패널 7.0 × 1.75 in,
`figure*`에 `width=\textwidth`. **값**: 같은 basename의 `.csv`(창마다 한 행).

**arm이 하나인 이유**: EXP-113이 이 워크로드를 Llama-3.1-70B 아닌 모델로 처음 돌린 것이고
**FluidServe만 통과했다.** 기준선이 없는 것은 그 정책들이 무엇을 해서가 아니라 아직 안 돌았기
때문이다. arm이 하나이므로 **두 attainment 패널의 간격이 곧 그 arm의 거절률**이고, 그것이
단일 arm 타임라인이 나란히 놓을 수 있는 유일한 쌍이다.

### ⚠ `exp109_hour.pdf`와 비교할 수 없다 — 이유는 모델이 아니라 trace다

Qwen2.5-72B가 이 하드웨어에서 더 느리므로 EXP-113은 **도착을 솎아낸 trace**
(`dyn60_shift_m2Am1B_b1045_q064.csv`, 같은 한 시간의 도착 64%)를 쓴다. 실측: **도착 63,818건 ·
평균 17.4 req/s** 대 Llama 쪽 **99,242건 · 27.1 req/s**. 믹스 일정과 15분 경계
(m2 → A → m1 → B, chat 93.0 → 33.3 → 76.9 → 60.0%)와 클래스 예산은 같다.
**즉 두 그림 사이에서는 모델과 제공 부하 둘이 동시에 다르고, 어느 차이도 둘 중 하나에
돌릴 수 없다.**

참고로 두 run의 한 시간 전체 값은 이렇다 — **비교가 아니라 각각의 기록으로만 읽는다**:

| | 모델 | 평균 도착률 | offered | admitted | 거절 | goodput | chat / dr / swe (offered) |
|---|---|---|---|---|---|---|---|
| EXP-113 | Qwen2.5-72B | 17.4 req/s | 81.3 | 99.9 | 18.6% | 12,266 | 90.7 / 66.8 / **58.5** |
| EXP-109 r2 | Llama-3.1-70B | 27.1 req/s | 79.3 | 100.0 | 20.7% | 11,587 | 83.7 / 69.7 / **73.1** |

⚠ **클래스 예산은 재조정하지 않았다** — chat (5 s, 50 ms) · deepresearch (10 s, 100 ms) ·
swe (7 s, 75 ms)로 Llama 때와 같다. **이것은 중립적이지 않은 선택이다**: 예산은 사용자에게 한
약속이므로 함대의 모델이 바뀐다고 움직이지 않는 것이 맞지만, 디코드 속도가 다른 모델은 같은
약속을 다르게 지킨다. **캡션이 예산을 재조정하지 않았다는 것을 적어야 한다.** 위 표에서 swe가
73.1 → 58.5로 내려간 것이 그 자리를 가리킨다(다만 부하도 같이 달라졌으므로 원인을 하나로
지목할 수 없다).

### 데이터와 run 선택

EXP-113(2026-09-03), 반복 하나. `/home/nxclab/tools/pick_usable_run.sh`가 고른다 —
**패턴에 세 디렉토리가 걸리는데 둘은 metrics 파일을 만들지 못하고 끝난 잔재다.**
채점은 문서 맨 앞의 고정 규칙(사다리, 관용 95%, goodput 토큰 단위)이고, 그래서 이 그림의
수치는 같은 규칙으로 채점된 다른 그림들과 같은 잣대 위에 있다.

### 캡션이 반드시 적어야 하는 것

- **모델이 Qwen2.5-72B이고 도착 trace가 64%로 솎아졌다.** 두 가지가 동시에 다르므로
  Llama 그림과의 차이를 어느 하나에 돌리지 않는다.
- **클래스 예산은 Llama 때와 같고 재조정하지 않았다.**
- **arm이 하나이고 반복도 하나다.** 반복 폭이 없으므로 이 곡선의 오르내림 중 어디까지가
  재현되는지는 아직 모른다.
- **세로 회색 점선은 15분마다 바뀌는 클래스 믹스의 경계다**(m2 → A → m1 → B).
- **(a)는 엔진이 보고한 생성 속도**이고 나머지는 클라이언트 기록이다.

**다시 만들려면**: `python3 paper_figures/fig_exp113_hour_qwen.py`

## `two_models_hour.pdf` / `_stacked.pdf` / `_reqgoodput.pdf` — 같은 한 시간을 두 모델에서, 네 패널

**스크립트**: `fig_two_models_hour.py`(`--stacked`가 오른쪽 패널을 도착 분해로,
`--req-goodput`이 요청 goodput 막대로 바꾼다).
**값**: 같은 basename의 `.csv`.

| 파일 | 크기 | 오른쪽 패널 |
|---|---|---|
| `two_models_hour.pdf` | 7.0 × 1.59 in | arm 색 막대 하나 = admitted 달성률, 반복 범위 표시 |
| `two_models_hour_stacked.pdf` | 7.0 × 1.92 in | 도착 전체의 네 갈래 누적 막대 |
| `two_models_hour_stacked_normfs_met.pdf` | 7.0 × 1.92 in | 같은 네 갈래를 **FluidServe가 규칙을 지킨 요청 수**로 나눈 것 |
| **`two_models_hour_reqgoodput.pdf`** | 7.0 × 1.59 in | **규칙을 지킨 요청 수 ÷ 구간 길이 = request goodput (req/s)**, 반복 범위 표시 |

### `_reqgoodput` 판 — 비율이 아니라 속도를 담은 막대 (2026-09-10 추가)

막대가 **그 구간에서 규칙을 지킨 요청 수를 구간 길이로 나눈 값**이라, 왼쪽 타임라인과
**같은 양을 두 단위로** 읽게 된다 — 마감을 지킨 토큰이 초당 몇 개인가와 마감을 지킨 요청이
초당 몇 건인가. 받아들인 요청 수가 arm마다 다를 때 비율끼리는 비교가 안 되므로, **"무엇을
얼마나 전달했는가"를 묻는 자리에서는 이 판을 쓴다.**

두 모델의 막대 축은 공유하지만 **두 반쪽은 여전히 비교가 아니다**(아래 절). 공유하는 이유는
한 반쪽의 다섯 막대를 서로, 그리고 그 반쪽의 도착률과 견주기 위해서다.

| 그린 값 (req/s, 반복 2 / 반복 1) | Llama, 도착 27.20 req/s | Qwen, 도착 17.45 req/s |
|---|---|---|
| **FluidServe** | **21.52 / 21.71** | **14.17 / 14.19** |
| llm-d | 15.33 / 13.56 | 9.95 / 9.56 |
| PolyServe | 14.53 / 14.08 | 10.54 / 10.49 |
| Llumnix SLO | 7.76 / 7.65 | 5.27 / 5.25 |
| vLLM router | 5.95 / 6.03 | 4.24 / 4.24 |

⚠ **Llama에서 2위를 매기면 안 된다** — llm-d 평균 14.45와 PolyServe 14.30의 차이가 0.15인데
**llm-d 자신의 두 반복이 1.78 벌어진다**(13.56 대 15.33). Qwen에서는 PolyServe가 앞서고
llm-d의 반복 폭이 0.39라 그쪽은 구별된다. **1위와 4·5위는 어느 모델에서도 안 바뀐다.**

⚠ **vLLM router는 같은 도착 스트림을 받지 않았다.** 거절을 안 해 부하 생성기가 밀렸고, 그린
구간에 도착한 요청이 **87,335건(Llama) / 56,073건(Qwen)**으로 다른 arm의 96,68x / 61,50x보다
**9~10% 적다.** 분모가 시간이므로 그 막대는 **더 얇은 스트림 위의 속도**이고 실제보다 유리하다.
누적 판에는 이 문제가 없다(각 막대가 그 arm 자신의 도착의 100%다).

둘 다 `figure*`에 `width=\textwidth`. 패널은 **(a) goodput 타임라인 + Request SLO 막대 =
Llama-3.1-70B**, **(b) 같은 쌍 = Qwen2.5-72B**이고, 캡션이 네 개가 아니라 **쌍마다 하나**다 —
쌍이 이 그림의 단위(한 모델의 시간당 속도와 그 뒤의 도착 분해)이기 때문이다.

### ⚠ 두 모델은 통제된 비교가 아니다

Qwen2.5-72B가 이 하드웨어에서 느리므로 그쪽 한 시간은 **도착을 64%로 솎아낸 trace**를 쓴다 —
**63,818건 대 99,242건, 평균 17.4 대 27.1 req/s**. 클래스 예산은 그대로다. **모델과 제공 부하가
동시에 다르므로 두 반쪽의 어떤 차이도 하나에 돌릴 수 없다.** 예: PolyServe의 SLO Attained가
Llama 53.4%에서 Qwen 60.4%로 오른 것에는 부하가 36% 줄어든 것이 같이 들어 있다.

### 막대가 재는 구간은 옆 타임라인이 그리는 구간과 같다

둘 다 같은 분에서 잘린다(Llama 59.2분, Qwen 58.8분). 절단 기준은 다른 한 시간 그림과 같다 —
창의 미완 비율 20%를 admission control이 있는 arm이 처음 넘는 지점이고, 없는 arm(vLLM router)은
그 표결에서 빠지되 **그 arm의 팽창을 실제로 재서** 0.00점임을 확인한다.

### 그려진 값 (stacked 판, 도착 전체의 %)

| 모델 | arm | SLO Attained | SLO Missed | Rejected | Unfinished |
|---|---|---|---|---|---|
| Llama-3.1-70B | FluidServe | **79.1** | 0.0 | 20.9 | 0.0 |
| Llama-3.1-70B | llm-d | **56.4** | 1.2 | 42.4 | 0.0 |
| Llama-3.1-70B | PolyServe | **53.4** | 28.5 | 18.0 | 0.0 |
| Llama-3.1-70B | Llumnix SLO | **28.5** | 32.0 | 39.4 | 0.0 |
| Llama-3.1-70B | vLLM | **24.2** | 29.2 | 0.0 | 46.6 |
| Qwen2.5-72B | FluidServe | **81.2** | 0.1 | 18.7 | 0.0 |
| Qwen2.5-72B | llm-d | **57.0** | 1.0 | 42.0 | 0.0 |
| Qwen2.5-72B | PolyServe | **60.4** | 1.4 | 38.2 | 0.0 |
| Qwen2.5-72B | Llumnix SLO | **30.2** | 26.2 | 43.5 | 0.1 |
| Qwen2.5-72B | vLLM | **26.7** | 19.2 | 0.0 | 54.1 |

admitted 판의 막대는 같은 구간의 admitted 달성률이고, CSV의 `admitted_pct`(그린 값)와
`admitted_pct_repeat1`(막대 위 범위 표시)에 있다.

### 네 갈래여야 하는 이유

"offered에서 거절 / 지킴 / 못 지킴" 세 갈래로 하면 **막대가 도착 합계가 되지 않는다** — offered
분모가 미완을 분모에서 빼는 정의이기 때문이고, **그 빠지는 몫이 가장 큰 자리가 하필 admission
control이 없는 arm**이다(Llama 46.6%, Qwen 54.1%). 빼면 그 arm의 막대만 다시 스케일되어 좋아
보인다.

### 색과 테두리 (2026-09-04에 여러 번 바뀌었다)

| 요소 | 값 | 왜 |
|---|---|---|
| SLO Attained | `#80cdc1` | 처음 초록 → 파스텔 → `#92c5de`(파랑)를 거쳤다. **파랑은 왼쪽 패널의 FluidServe 선과 충돌해서 버렸다** — 한 그림에서 파랑이 왼쪽에서는 정책, 오른쪽에서는 결과를 뜻하게 된다 |
| SLO Missed | `#f4a582` | 위와 명도가 거의 같아(0.71/0.74) 두 띠가 "하나와 그 그림자"로 읽히지 않는다 |
| Rejected | `#cfcfcf` | **두 색보다 밝게** 둔다. 아무것도 서비스되지 않은 띠에 눈이 먼저 가면 안 된다 |
| Unfinished | 흰색 + 빗금 | 결과가 아니라 결과의 부재이므로 색을 주지 않는다 |
| 타임라인 선 | arm 색을 **20% 밝게** | 색상은 안 움직이므로 논문의 다른 그림과 같은 arm으로 읽힌다. **이 그림만 밝게 그린다** |
| 테두리 | 네 패널 모두 **왼쪽·아래만** | 프로젝트 스타일은 네 테두리 전부다. ⚠ 막대가 위쪽에서 선 없이 끝나므로 **막대가 도착의 100%라는 것을 캡션이 적어야 한다** |

### 크기가 두 번 줄었다

패널 높이를 85%로, 다시 10% 줄였다(2.25 → 2.04 → 1.92 in). **위·아래 두 띠는 글자가 들어 있어
인치를 고정**하고 패널만 높이를 냈다 — `_rect(height)`가 그 인치를 `tight_layout`이 원하는
분수로 바꾼다. 막대 패널의 폭도 5% 줄였고(0.56 → 0.532), 막대 사각형은 칸의 0.72라 패널과 같이
좁아진다.

### 캡션이 반드시 적어야 하는 것

- **두 모델은 비교가 아니다.** 도착 trace가 64%로 다르고 예산은 같다.
- **막대는 도착 전체의 100%다**(stacked 판). 위 테두리가 없으므로 그림이 그것을 말하지 않는다.
- **미완 띠가 무엇인지** — 창이 닫힐 때까지 안 끝나 결과를 모르는 요청이고, 논문의 다른 곳에서
  달성률 분모 양쪽에서 빠지는 그 인구다.
- **arm당 run 하나**(반복 2)다. admitted 판의 범위 표시만 반복 1을 담고 있고, stacked 판에는
  반복 폭이 그려져 있지 않다.
- **모델 이름은 Llama-3.1-70B와 Qwen2.5-72B다**(80B가 아니다).

**다시 만들려면**: `python3 paper_figures/fig_two_models_hour.py [--stacked [--norm-to-fs met]] [--req-goodput]`

⚠ **세 판이 같은 `.csv` 열을 공유한다** — 2026-09-10부터 어느 판을 돌리든 `met_requests`,
`met_requests_repeat1`, `arrivals`, `req_goodput_s`, `req_goodput_s_repeat1`이 같이 쓰인다.
기존 열의 값은 바뀌지 않았고(누적 판은 픽셀 단위로 동일하다), 열이 다섯 개 늘었을 뿐이다.

## `engine_state_2x2_t75.pdf` / `_t75_withfs.pdf` — 점수 밑에서 엔진이 무엇을 하고 있었나, 토큰 단위 deadline 판의 run으로

**스크립트**: `fig_engine_state_t75.py`. **값**: 같은 basename의 `.csv`.
**크기**: 3.335 × 3.05 in, `figure`에 `width=\columnwidth`.

패널 넷은 전부 엔진 자신의 Prometheus 시리즈다 — **(a) preemption**
(`vllm:num_preemptions_total`의 조건 내 증가분, 엔진 넷 합), **(b) KV 점유율 p90**
(`vllm:kv_cache_usage_perc`), **(c) 대기 큐 p90**(`vllm:num_requests_waiting`),
**(d) running batch p90**(`vllm:num_requests_running`). **채점이 들어가는 값이 하나도 없다** —
이름의 `t75`는 채점 규칙이 아니라 **어느 run 집합인지**를 가리킨다.

### 왜 새로 그렸나 — `engine_state_2x2_fs.pdf`와 무엇이 다른가

기존 `engine_state_2x2_fs.pdf`(`fig_preemption_kv.py`, `fig_intro_capacity.py`의 glob)는
**2026-08-13에 끝난 앞의 정적 sweep**을 그리고, 거기서 swe의 약속은 **전체 시간 30초**였다.
이 그림은 **`motivation_throughput_vs_goodput_4panel_t75_withfs.pdf`가 그리는 바로 그 run들**을
그린다 — swe를 **TTFT 7초 + 토큰당 75 ms**로 모든 arm에 알려준 EXP-108(2026-08-31)과, 그 옆에
붙는 vLLM router의 EXP-77이다. **점수 패널과 엔진 패널이 같은 run을 설명하게 하려는 것이고,
그래서 두 그림은 같은 arm의 같은 도착률에서 다른 값을 갖는다.** 파일 이름의 `t75`가 그 구분이다.

**run 목록을 glob이 아니라 채점표에서 읽는다** — `results/aggregate_analysis/ladder95/`의
`exp108_paper_ladder95.csv`와 `vllm77_ladder95.csv`의 `run` 열이다. 그래서 EXP-108의 llm-d
10 req/s 칸에 들어간 EXP-110 대체(반복 둘이 거절률 0.0%와 42.0%로 갈렸고 재측정 셋이 전부
0.0%라 42.0%를 이상치로 보고 EXP-110 반복 3·5로 바꾼 것, `build_paper_ladder_table.py`)가
**점수 그림과 엔진 그림에 똑같이 적용된다.**

### 그려진 값 (일부; 전부는 CSV에)

| arm | preempt @35 | @70 | KV p90 @35 | @70 | 큐 p90 @35 | @70 | batch p90 @70 |
|---|---|---|---|---|---|---|---|
| vLLM-router | 3,484 | 4,029 | 100 | 100 | 625 | 4,626 | 358 |
| PolyServe | 0 | 0 | 56 | 57 | 2 | 2 | 258 |
| Llumnix SLO | 484 | 1,494 | 98 | 100 | 20 | 50 | 216 |
| llm-d | 0 | 28 | 49 | 55 | 1 | 1 | 107 |
| FluidServe | 198 | 154 | 70 | 58 | 4 | 5 | 217 |

### ⚠ PolyServe가 앞 sweep과 크게 다르다 — 원인은 여기서 확정되지 않았다

앞 sweep에서 PolyServe는 25~55 req/s에서 preemption 2,268~2,842회, KV p90 64.7~99.9%,
큐 p90 311~2,156이었다. 이 sweep에서는 **preemption 0, KV p90 56, 큐 p90 2**다. 같은 sweep의
채점표를 보면 이 arm이 **35 req/s에서 도착의 10.8%, 70 req/s에서 55.6%를 거절**하고 있으므로
엔진에 들어가는 일 자체가 줄어든 것과 방향이 맞지만, **swe 약속의 형태가 바뀐 것(전체 30초 →
TTFT 7초 + 토큰당 75 ms)이 tier 배정과 admission 판정 조건을 동시에 바꾸므로 둘 중 무엇이
얼마나 기여했는지는 이 그림으로 갈리지 않는다.** 두 sweep의 PolyServe 수치를 한 문장에 넣지
않는다.

### ⚠ vLLM router는 다른 세션이고 칸 둘이 반복 하나다

45와 70 req/s가 n=1이다(45는 반복 2의 디렉토리가 있는데 `server_metrics/engine_*.jsonl`이
없어서 빠졌고, 70은 애초에 반복 하나다). **오차막대가 없는 점은 가장 정밀한 점이 아니라 가장
덜 정밀한 점이다.** 스크립트가 빠진 run을 이름까지 찍고 CSV에 `n_repeats`를 남긴다.

### ⚠ 기존 `engine_state_2x2_fs.pdf`는 지금 다시 그리면 그때 그림이 아니다

그 그림의 docstring은 "여덟 도착률 전부에서 모든 arm이 반복 2회"라고 적고 있는데, **지금
디스크에서는 80개 중 27개가 `server_metrics/engine_*.jsonl`을 갖고 있지 않다.** 그래서 지금
다시 돌리면 기준선 대부분의 칸이 n=1이 되고 **PolyServe 45·70, vLLM 45, Llumnix SLO 55는 칸
자체가 사라진 채 조용히 점이 빠진 곡선**이 나온다. 파일은 그 run들이 살아 있던 때 그려진
것이므로 **파일은 유효하고 재생성이 유효하지 않다.** 이 문서를 쓴 시점에 그 스크립트는 고치지
않았다.

## `class_mix_static.pdf` / `_share.pdf` — 각 컨트롤플레인이 클래스를 엔진에 어떻게 섞었나, 도착률별로

**스크립트**: `fig_class_mix_static.py`. **값**: `class_mix_static.csv`(두 판이 같은 표를 쓴다 —
share 판은 각 막대를 자기 합으로 나눈 것이라 CSV를 따로 두지 않는다).
**크기**: 7.0 × 2.75 in, `figure*`에 `width=\textwidth`.

행 = 도착률 셋(20 / 35 / 45 req/s), 열 = arm 다섯, 칸마다 **엔진 넷의 stacked bar**.
막대 높이는 **그 엔진에 동시에 상주한 요청 수의 시간 평균**이고, share 판은 그것을 100%로
정규화한 것이다.

**왜 dispatch 개수가 아니라 상주(residency)인가**: 주장하려는 것이 "같은 시각에 한 엔진 위에
무엇이 같이 있었나"이기 때문이다 — 어떤 인스턴스가 가장 빡빡한 클래스의 요청을 하나라도
들고 있으면 그 인스턴스의 허용 속도가 그 클래스의 예산으로 묶인다. dispatch 개수는 짧은 요청이
많은 클래스와 엔진을 몇 분씩 점유하는 긴 요청의 클래스를 같은 무게로 센다. 둘 다
`engine_class_occupancy.py`가 내고 CSV에 `dispatched`도 있다.

### ⚠ 막대는 포트 번호가 아니라 **그 창에서 chat을 얼마나 들었는지**로 정렬돼 있다

막대 1 = **그 60초 창에서 chat을 가장 많이 든 엔진**, 2 = 그 다음, …이고 정렬을 창마다 다시
한 뒤 창 평균을 낸다. 이유가 둘이고 둘째가 본질적이다.

1. **포트 번호는 정체가 아니다.** 어느 엔진이 빡빡한 클래스를 갖게 되는지는 결과이지 이름이
   아니고, 반복마다 arm마다 다른 포트다. 포트로 평균 내면 서로 다른 것을 섞는다.
2. **정적 조건 안에서도 배치가 움직인다.** Llumnix SLO는 55 req/s에서 swe의 유효 인스턴스 수가
   **창별 1.88 대 조건 전체로 합치면 3.98**이다 — 그 클래스를 든 두 엔진이 여섯 번 바뀌었기
   때문이다. 포트로 합치면 그 arm이 네 엔진에 고르게 뿌린 것으로 그려지는데, 매 순간의 사실은
   그 반대다.

**검증은 조건마다 `static_summary.csv`에 있다** — 클래스별로 창별 유효 인스턴스 수 / 조건 전체로
합친 값 / 최다 보유 엔진이 바뀐 횟수. 그린 세 도착률에서는 두 값이 대체로 붙는다(FluidServe
chat 1.10 대 1.12 @20, 2.82 대 2.85 @45; PolyServe swe 1.50 대 2.13 @20, 1.00 대 1.14 @45)
— **정렬이 결과를 바꾸지 않는 구간이라는 것이 확인된 것이고, 확인 없이 합치는 것과는 다르다.**

### ⚠ 분모는 받아들여진 일이다

거절된 요청은 엔진이 없으므로 이 그림에 아예 없다. 45 req/s의 거절률은 FluidServe 30.4%,
PolyServe 28.3%, llm-d 61.5%, Llumnix SLO 73.0%, vLLM router 0.0%이고, **거절률을 캡션에 같이
적지 않으면 "적게 받은 arm의 막대가 낮다"를 성능으로 오독한다.**

### ⚠ 한 칸이 부분 집합 위에 그려진다

엔진 귀속은 스케줄러 배치 로그를 거치는데 그 줄이 고부하에서 사라진다. 그린 칸은 전부
99.8~100%인데 **vLLM router 45 req/s만 85.2%**이고 그 칸은 n=1이다(다른 반복은 스케줄러 메트릭이
아예 없다). 그림이 45에서 멈추는 이유가 이것이다 — **55에서 vLLM 69.0%, 70에서 vLLM 52.3% ·
Llumnix SLO 54.8%**로 더 떨어진다. 칸 안에 귀속률을 찍고 CSV에 `attributed_pct`가 있다.

### 데이터

EXP-108(2026-08-31) 네 arm + EXP-77(2026-08-10) vLLM router, **점수 그림과 같은 79 run**.
`build_class_mix_tables.py`가 표를 만들고 그림은 표만 읽는다. 창 60초, 분석 창은 `load_run`의
것(warmup 60초·drain 20초 제외). 칸마다 반복 2회(vLLM 45 req/s만 1회).

## `class_mix_hour.pdf` / `_abs.pdf` — 같은 질문을, 도착률과 믹스가 둘 다 움직이는 한 시간에서

**스크립트**: `fig_class_mix_hour.py`. **값**: `class_mix_hour.csv`(두 판 공통).
**크기**: 7.0 × 3.10 in, `figure*`에 `width=\textwidth`.

행 = 인스턴스 넷(`Instance 1`~`4`), 열 = arm 다섯, x = 분, 채움 = 클래스별 상주량의 stacked area.
y축 이름은 `Class share rate per instances (%)`. **행의 순서는 한 시간 전체의 chat 상주량**이라
**행 하나가 한 인스턴스이고**, 배치가 바뀌면 그 행의 색이 도중에 바뀌는 것으로 나타난다.
`_abs.pdf`는 같은 값을 요청 수로 그린 것이다.

### ⚠ 이 그림만 클래스 색이 다르다 (2026-09-07 변경)

`Chat #fc8d59` / `Deep Research #ffffbf` / `Agent #91bfdb` (세 단계 RdYlBu)이고, **이 저장소의
공통 클래스 색**(`exp22_fluidserve.CLASS_COLORS`: chat `#1f77b4`, deepresearch `#ff7f0e`,
swe `#d62728`)**이 아니다.** stacked area는 큰 면을 색으로 채우므로 채도가 높은 tab10 셋은
범주가 아니라 경고로 읽힌다는 것이 이유다. **대가를 그대로 적는다: 같은 색이 옆 그림
`class_mix_static.pdf`에서는 다른 클래스를 뜻한다.** 둘을 한 페이지에 놓으려면 정적판도 이 색으로
옮기거나, 둘을 같이 놓지 않는다.

**패널 안의 띠에는 테두리를 그리지 않는다** (2026-09-07). 두 띠의 경계는 한 색이 끝나고 다음
색이 시작하는 자리이고, 거기에 선을 하나 더 그으면 셋을 그리는 패널에 넷째 것이 생긴다.
**범례 swatch에만 0.4 pt 회색 테두리를 남긴다** — `#ffffbf` 정사각형은 흰 지면 위에서 테두리가
없으면 아무것도 아닌 것으로 보인다. swatch는 `handlelength`와 `handleheight`를 같게 두어
**정사각형**이다.

⚠ 그 대가: **패널에 top spine이 없으므로 100%까지 채운 띠의 위쪽 경계는 그려지지 않는다**
(PolyServe의 `Instance 4`가 한 시간 내내 그 상태다). y축 눈금 100이 그 자리를 말해 준다.

**정적판이 할 수 없는 것을 이 그림이 한다**: 정적 조건은 8분 동안 믹스가 고정이지만 여기서는
믹스가 15분 세그먼트로 움직이고 도착률이 production trace를 따른다. 그래서 **한 번 정한 배치가
계속 맞는가**를 묻고, 한 번 정하고 다시 안 보는 정책은 **한 시간 내내 색이 안 변하는 네 띠**로
나타난다(PolyServe 엔진 4가 정확히 그렇다 — 한 시간 전부 deepresearch 100%).

**한 시간을 막대 하나로 합치면 안 되는 이유**는 위와 같고 여기서 더 크다. `hour_summary.csv`:
FluidServe chat이 **창별 1.64 대 합치면 2.16**(홀더 4번 바뀜), llm-d chat이 **3.45 대 3.96**
(30번), PolyServe swe가 **1.05 대 1.98**(1번). **어느 arm도 합친 값으로 설명되지 않는다.**

### ⚠ 세 가지를 캡션에 같이 적는다

- **분모는 받아들여진 일**이고 이 run의 거절률은 FluidServe 19.9%, PolyServe 22.5%,
  Llumnix SLO 39.4%, llm-d 49.0%, vLLM router 0.0%다. 그래서 **share 판이 곧바로 비교되는 판**이고,
  `_abs.pdf`에서는 **vLLM router 열만 한 엔진에서 약 7,000건까지 올라간다**(다른 넷은 200~430).
  그 열은 **자기 y 스케일**을 쓰고 스케일 값을 패널에 찍는다.
- **상주는 "그 엔진에 배정됐다"이지 "그 엔진에서 실행 중"이 아니다.** 클라이언트가 보낸 시각부터
  끝난 시각까지를 세므로 그 엔진의 큐에서 기다린 시간이 포함된다. 거절하지 않는 arm이 배치 크기가
  아니라 수천 건으로 읽히는 이유가 이것이다.
- **vLLM router 열은 admitted의 74.9% 위에 그려진다**(나머지 넷은 99.9~100%).
  ⚠ **2026-09-07에 그 숫자를 그림에서 뺐으므로 캡션이 통째로 짊어진다** — 아래의 시간 프로파일까지
  포함해서다. 스크립트는 arm마다 그 값을 여전히 출력한다.

### 캡션 (2026-09-07)

> **Figure N: Class mixture on each instance over the hour.** Each row is one
> instance for the whole hour; within a column the four instances are sorted once,
> by the chat each of them held over the run, because an instance's port number
> means nothing across control planes. A band is one class's share of the requests
> resident on that instance in a 60 s window. PolyServe fixes the assignment for
> the whole hour, Llumnix SLO and llm-d place every class on every instance, and
> FluidServe separates the classes and moves the boundary as the mixture shifts.
> Admitted requests only; white marks a window with no resident request.

⚠ **정적판과 정렬 방식이 다르다.** `class_mix_static.pdf`는 **창마다 다시 정렬**해서 막대 1이
"그 창에서 chat을 가장 많이 든 인스턴스"이고, 이 그림은 **한 시간에 한 번 정렬**해서 행 하나가
한 인스턴스로 고정된다. 그렇게 해야 시간축이 연속이고, 배치가 바뀌면 그 행의 색이 도중에
바뀌는 것으로 보인다. 캡션에서 이 차이를 흐리면 두 그림의 x축이 같은 뜻으로 읽힌다.

**캡션에서 뺀 것은 본문이 져야 한다 — 지운 것이 아니다.** 넷이다: ① 거절률
(FluidServe 19.9 / PolyServe 22.5 / Llumnix SLO 39.4 / llm-d 49.0 / vLLM 0.0%),
② vLLM router의 귀속률 74.9%와 그것이 뒤쪽 절반에 몰려 있다는 것, ③ 상주의 정의
(배정된 시점부터 완료까지, 그 인스턴스에서 기다린 시간 포함), ④ 반복이 하나라는 것.
①과 ②는 **그림을 읽는 조건**이므로 본문에서 이 그림을 처음 가리키는 문단에 있어야 한다.

### ⚠ vLLM router의 74.9%는 한 시간에 고르게 퍼져 있지 않다 (2026-09-07 측정)

열 제목의 `75% attr.`은 **그 arm이 받아들인 요청 중 어느 엔진이 처리했는지 알아낸 비율**이다.
요청은 전부 `metrics.csv`에 있고 점수도 전부 매겨져 있다 — **없는 것은 엔진 이름 하나**이고,
그것은 스케줄러 배치 로그의 `[Schedule] dispatch request <uuid> to ... instance <id>` 줄에서만
오는데 그 줄이 고부하에서 사라진다.

**그 손실이 시간에 균등하지 않다는 것을 이 그림을 위해 처음 쟀다** (반복 1, 분 단위):

| 구간 | 귀속률 | 그 구간의 admitted |
|---|---|---|
| 0~20분 | **99.7%** | 28,730 |
| 20~40분 | 89.9% | 31,640 |
| **40분~끝** | **43.6%** | 37,985 |

63분 중 35분이 100%이고 90% 아래로 처음 내려가는 것은 **22분**이다. 분당 도착 수와 귀속률의
상관은 −0.37이다.

**그리고 손실이 클래스에 중립적이지도 않다**: 귀속 안 된 요청은 deepresearch가 29.3%인데 귀속된
요청은 18.9%다(chat은 60.3 대 67.2%). 따라서 **vLLM router 열의 뒷부분은 표본이 4할대이고
deepresearch를 실제보다 적게 그린다.** 앞의 3분의 1은 사실상 전수다.

→ **이 열에서 읽어도 되는 것**은 "엔진 3·4가 agent를 더 많이 들었다" 같은 **앞 구간의 구조**이고,
**읽으면 안 되는 것**은 40분 이후 이 열의 클래스 비율을 다른 arm의 같은 구간과 나란히 놓는 것이다.
정적판(`class_mix_static.pdf`)이 45 req/s에서 멈추는 것도 같은 이유이며, 거기서는 이 arm이 85.2%다.

### 데이터

EXP-109(2026-08-31) 다섯 arm의 **반복 1**. 반복 2도 같은 표에 있고 요약이 재현을 기록한다
(FluidServe chat 창별 1.64 대 1.53, PolyServe swe 1.05 대 1.26). 창 60초, 분석 창은 `load_run`의 것.

## `class_capacity_hour.pdf` / `_owner.pdf` — 도착한 일의 양과, 그것을 각 클래스가 함대에서 얼마나 받았나

**스크립트**: `fig_class_capacity_hour.py`. **값**: `class_capacity_hour.csv`(두 판 공통).
**크기**: 7.0 × 2.75 in, `figure*`에 `width=\textwidth`.

`class_mix_hour.pdf`는 패널마다 100%로 정규화하므로 **분리 여부는 보여주고 양은 지운다** —
요청 넷을 든 인스턴스와 400건을 든 인스턴스가 같아 보인다. 이 그림이 양을 되돌린다.

- **위 패널(한 줄, 전체 폭)**: 도착한 요청, 초당, 클래스로 stack. 다섯 arm이 같은 trace를
  재생하므로 한 번만 그리고 아래 다섯 패널을 전부 이것에 대고 읽는다.
- **아래 다섯 패널**: 각 클래스가 받은 **인스턴스 수**.
  - `class_capacity_hour.pdf`(**분수**): `a_c(t) = Σ_i r_{c,i}(t) / r_i(t)`. 한 클래스만 든
    인스턴스는 그 클래스에 1, 셋을 3분의 1씩 든 인스턴스는 각 클래스에 3분의 1을 준다.
  - `class_capacity_hour_owner.pdf`(**정수**): 그 창에서 **한 클래스가 그 인스턴스 상주의 50%
    이상**을 차지하면 그 클래스가 그 인스턴스를 갖는다. 아무 클래스도 못 넘으면 **Mixed(회색)**.
- 두 판 모두 **stack의 합이 4에 못 미치면 그 창에 아무것도 안 든 인스턴스가 있다는 뜻**이다.

### 무엇이 읽히나 (15분 세그먼트 평균, 인스턴스 수 / 4)

도착: `0-15분` chat 22.7 · dr 1.1 · agent 0.6 req/s → `15-30분` 8.3 · 8.3 · 8.3 →
`30-45분` 20.6 · 4.1 · 2.1 → `45-60분` 19.7 · 9.9 · 3.3.

| arm (분수 판) | 0-15분 | 15-30분 | 30-45분 | 45-60분 |
|---|---|---|---|---|
| PolyServe | 1.7 / 1.0 / 0.7 | 1.2 / 1.2 / 1.5 | 1.7 / 1.5 / 0.8 | 1.0 / 2.1 / 0.9 |
| FluidServe | 2.6 / 0.8 / 0.5 | 0.7 / 2.0 / 1.2 | 1.7 / 1.7 / 0.5 | 1.4 / 2.2 / 0.4 |
| Llumnix SLO | 3.5 / 0.4 / 0.1 | 0.1 / 3.3 / 0.6 | 2.0 / 1.6 / 0.4 | 0.2 / 3.6 / 0.2 |

**도착의 93%가 chat인 첫 15분에 PolyServe는 함대의 43%(1.0 + 0.7 인스턴스)를 도착의 7%인 두
클래스에 묶어 둔다.** 같은 구간에서 FluidServe는 chat에 2.6을 준다.

### ⚠ 위 패널과 아래 패널은 단위가 다르다 — 1:1로 겹쳐 읽으면 안 된다

위는 **도착 요청 수**, 아래는 **상주(= 인스턴스 위에서 보낸 시간)**다. 클래스별 평균 입력이
chat 677 · deepresearch 4,505 · swe 6,805 토큰이라 **요청 하나가 차지하는 인스턴스 시간이 클래스마다
몇 배씩 다르고**, 게다가 **밀리고 있는 클래스는 그만큼 더 오래 상주하므로 정책에 따라 더 커진다**.
저부하 구간에서 잰 서비스 시간(chat 18.0 · dr 39.0 · swe 19.1초)으로 도착을 일감으로 환산해도
`45-60분`의 chat 몫이 요청 60% → 일감 44%로 바뀔 뿐, Llumnix SLO의 chat 상주 몫 5%와는 여전히
다르다. **그 차이가 곧 그 정책이 chat을 빠르게 비우고 deepresearch를 오래 붙들고 있다는 사실이다.**
→ 읽어야 하는 것은 **"위가 움직일 때 아래가 따라 움직이는가"**이지 두 높이의 일치가 아니다.

### ⚠ 이 그림 하나로 FluidServe와 섞는 정책을 구분할 수 없다

완전히 섞는 정책도 각 클래스의 몫을 **자동으로** 도착에 비례시킨다(모든 인스턴스가 같은 구성이므로).
그래서 이 그림이 가르는 것은 **"움직이는가"**뿐이고, PolyServe만 뚜렷하게 못 움직인다.
**"움직이면서 동시에 분리돼 있는가"는 `class_mix_hour.pdf`가 답한다.** 둘은 한 주장의 두 반쪽이므로
같이 싣거나, 싣지 않는 쪽을 본문에서 수치로 말한다.

⚠ 아래 패널은 **admitted**만 셀 수 있다(거절된 요청은 인스턴스에 도달하지 않는다). 위 패널은
**offered**다. 이 run의 거절률은 FluidServe 19.9 / PolyServe 22.5 / Llumnix SLO 39.4 /
llm-d 49.0 / vLLM router 0.0%다. vLLM router 열은 admitted의 74.9% 위에 그려진다.

### 데이터

EXP-109(2026-08-31) 다섯 arm의 반복 1. 도착 패널은 `260831_2015_exp109r1_fsv3capgnofrct75_shift`의
`metrics.csv`에서 오고(다섯 arm이 같은 trace라 어느 것을 써도 같다), 인스턴스 몫은
`build_class_mix_tables.py`의 표에서 온다. 창 60초, 분석 창은 `load_run`의 것.

## `class_mix_outcome_hour.pdf` — 배치와 그 결과를 한 그림에

**스크립트**: `fig_class_mix_outcome_hour.py`. **값**: 아래 줄은 `class_mix_outcome_hour.csv`,
위 네 줄은 `class_mix_hour.csv`. **크기**: 7.0 × 3.60 in, `figure*`에 `width=\textwidth`.

열 = 컨트롤플레인 다섯, **위 네 줄 = `class_mix_hour.pdf`의 인스턴스별 클래스 구성**,
**아래 한 줄 = 그 배치가 만든 결과** — 엔진이 만든 토큰(회색)과 그중 자기 deadline을 지킨 토큰
(arm 색). **둘 사이의 회색 면적이 하고도 아무 값이 없었던 일이다.**

**왜 두 반쪽을 한 그림에 두나**: 배치가 서로 다르다는 것만으로는 어느 배치가 나은지 알 수 없다.
아래 줄은 **같은 함대·같은 trace·같은 엔진**을 같은 규칙으로 채점한 것이므로, 열 사이의 차이는
컨트롤플레인이 만든 차이다. **열을 위에서 아래로 읽으면 기전과 결과가 이어진다.**

### 그려진 값 (한 시간 창 평균, 토큰/초)

| arm | 만든 토큰 | 제때 온 토큰 | 제때 온 비율 |
|---|---|---|---|
| **FluidServe** | **11,839** | **11,588** | **97.9%** |
| PolyServe | 10,923 | 8,463 | 77.5% |
| Llumnix SLO | 10,043 | 6,825 | 68.0% |
| llm-d | 8,025 | 7,883 | 98.2% |
| vLLM router | 6,977 | 2,738 | 39.2% |

**FluidServe는 가장 많이 만들면서 거의 버리지 않는다.** llm-d는 버리는 비율은 같지만
**만드는 양이 8,025로 3,814 적고**(도착의 49.0%를 거절한다), PolyServe는 만드는 양이 비슷한데
**22.5%를 버린다.** vLLM router는 거절이 0%인데 **만든 토큰의 60.8%가 늦게 도착한다.**

### ⚠ 아래 줄이 가르지 않는 것

goodput은 **거절해도** 줄고 **받아 놓고 늦어도** 줄어서, 두 원인이 같은 모양으로 나타난다.
이 run의 거절률은 FluidServe 19.9 / PolyServe 22.5 / Llumnix SLO 39.4 / llm-d 49.0 /
vLLM router 0.0%이고 **캡션이 이것을 같이 말해야 한다.** 요청 단위 분해는
`exp109_hour_five_admitted.pdf`에 있다.

### 데이터

EXP-109(2026-08-31) 반복 1, **위아래가 같은 run**이다(그래서 두 그림을 나란히 놓는 대신 하나로
합칠 수 있다). 배치는 60초 창(`build_class_mix_tables.py`), goodput은 ladder verdict를 90초 창
30초 간격으로(`deadline_ladder_attainment.py`가 규칙의 주인), throughput은 네 엔진의
`vllm:generation_tokens_total`이다. **vLLM router의 위 네 줄만 admitted의 74.9% 위에 그려지고
아래 줄은 온전하다** — goodput과 throughput은 클라이언트 기록과 엔진 카운터라 엔진 이름이 필요없다.

## `class_mix_hour_abs_tbt.pdf` — 무엇을 들고 있었나 옆에 얼마나 빨리 돌았나 (2026-09-10)

**스크립트**: 같은 `fig_class_mix_hour.py`에 `--tbt`.
**크기**: 7.0 × 3.10 in, `figure*`에 `width=\textwidth`. 값은 같은 basename의 `.csv`.

| 파일 | 크기 | 열 | 축 숫자 | 지연 축 |
|---|---|---|---|---|
| `class_mix_hour_abs_tbt.pdf` | 7.0 × 3.10 in | arm 다섯 | 왼쪽 0·최대, 오른쪽은 마지막 열에만 | 로그, 30/100/300 |
| **`class_mix_hour_abs_3arms_tbt.pdf`** | **3.335 × 2.60 in, 한 열** | **PolyServe / Llumnix SLO / llm-d** | **양쪽 다 모든 패널에**(왼쪽 0·중간·최대) | **선형 0~150, 50 간격** |

```
python3 paper_figures/fig_class_mix_hour.py --tbt
python3 paper_figures/fig_class_mix_hour.py --tbt --col --palette pubugn \
        --tbt-max 150 --label-size 5 \
        --arms polyservept75 slot75 llmdslot75 --suffix _3arms
```

2026-09-10에 이름 셋을 바꿨다: 왼쪽 축이 **`Number of Requests per Instance`**(넓은 판은 뒤에
`(own scale per column)`이 붙는다), 아래 축이 **`Time (min.)`**, 그리고 패널의 눈금·축 글씨가
`--label-size`로 조절된다(한 열 기본값 6, 이 판은 **5**).
⚠ **5 pt는 이 저장소가 쓰는 8 pt보다 작고 학회가 요구하는 최소 크기에 가깝다.** 그림을 축소해서
넣으면 그만큼 더 작아지므로 **`width=\columnwidth`로 배율 없이** 넣어야 한다.

한 열 판은 클래스 색이 **ColorBrewer PuBuGn 세 단계**(chat `#ece2f0`, deep research
`#a6bddb`, agent `#1c9099`, `--palette pubugn`)이고 **지연 선이 빨간 점선**이다.
⚠ **이것은 순차(sequential) 램프라 다른 두 팔레트와 성질이 다르다** — 세 단계가 주로 밝기로
갈리므로(233 / 187 / 137) 눈이 **순서**로 읽고, 읽는 사람이 "agent가 큰 쪽"이라고 받아들일 수
있다. 세 클래스는 순서가 없는 범주이므로 캡션이 그 램프에 기대면 안 된다. 대신 **회색조에서
세 띠가 갈리는 유일한 팔레트다**(경계 밝기 차 46 / 50, deep research가 없는 구간에서 맞닿는
chat|agent는 96).
⚠ **빨강은 이 저장소에서 PolyServe의 색이다.** 이 그림에서는 arm이 열 제목으로 이름을 갖고
클래스가 파랑·청록이라 패널 안에 다른 빨강이 없지만, arm 색을 쓰는 그림과 한 페이지에 놓이면
같은 잉크가 두 뜻을 갖는다.

⚠ **지연 축을 0~150 ms로 고정하면 잘리는 것이 생긴다.** 이 세 arm에서는 **42,744초 중
10초(0.023%)**가 위로 나가고 전부 PolyServe다(인스턴스 1이 3.5분에 223.7 ms, 인스턴스 2가
14.5분에 261.7 ms — 둘 다 클래스 구성이 바뀌는 순간의 한두 초다). **잘린 선은 천장에 닿아
머문 선처럼 읽히므로** 스크립트가 잘린 초를 세어 찍고 캡션이 그것을 적어야 한다. 다섯 arm
판이 로그 축인 것은 admission control이 없는 arm이 한 엔진에서 중앙값 398.5 ms이기 때문이다.

⚠ **한 열에서는 왼쪽 축 이름이 짧아진다** — `Requests resident per instance (own scale per
column)`은 잉크가 3.4 in이라 2.60 in 캔버스에서 양끝이 잘렸다(회전된 그림 라벨은 아무것도
자르지 않으므로 조용히 잘린다). 괄호 안의 말은 캡션으로 옮기고, `check_side_labels`가 양쪽
라벨의 여유를 빌드마다 찍는다.

열이 셋 이하이면 `dense_y`가 켜져 **양쪽 축의 숫자가 패널마다 붙는다** — 다섯 열에서는 같은
눈금을 다섯 번 적는 것이 한 가지 정보에 다섯 배의 잉크였는데, 셋이면 자리가 있고 위아래
패널을 견줄 때 눈금을 그림 반대편에서 들고 오지 않아도 된다.

**띠는 `class_mix_hour_abs.pdf`와 같은 값이고 오른쪽 축만 새로 생긴 것이다.** 선은 그
인스턴스의 **토큰당 평균 시간**으로, 엔진 자신의 `vllm:inter_token_latency_seconds_{sum,count}`
증분의 비(그 초에 그 엔진이 낸 토큰으로 가중한 평균)를 **120초 박스 필터로 평활**한 것이다
(2026-09-10에 60 → 120초. 띠가 60초 창이라 처음에는 해상도를 맞췄는데, 한산한 초에 느린 토큰
몇 개가 만드는 분 단위 요동이 남아서 늘렸다. **표본에 중심을 둔 박스 필터라 실제 계단은
2분짜리 경사로 그려지고, run의 처음과 끝 1분은 더 적은 표본으로 평균된다.**)
시각 기준점은 mix 표와 같다(loader가 보는 분석 창의 첫 도착).

**행이 엔진 rank이고 메트릭 파일은 포트**라, mix 표가 run 전체에 대해 포트마다 매긴 rank로
둘을 잇는다. rank 규칙이 바뀌면 띠와 선이 같이 움직인다.

### ⚠ 오른쪽 축이 로그다

한 그림에 축 하나를 쓰는 것이 맞다(지연은 패널마다 달라지지 않는 예산에 견주는 양이다).
그런데 **admission control이 없는 arm이 한 엔진에서 중앙값 400.8 ms**인데 나머지 패널은
20~90 ms 사이에 있어서, 선형 축이면 네 열이 바닥에 붙은 직선이 된다. 로그 축에서는
FluidServe의 44 ms 대 71 ms 차이가 그대로 읽히면서 400 ms 엔진도 같은 격자 위에 있다.
눈금은 30 / 100 / 300이고 **마지막 열에만 숫자를 적는다.**

### ⚠ 선은 그 인스턴스의 모든 클래스를 합친 하나의 값이다

클래스별 값이 아니므로 **한 클래스의 예산과 직접 견주면 안 된다.** chat 50 / deepresearch 100 /
swe 75 ms인데 선은 셋을 섞은 토큰 가중 평균이다. 70 ms인 패널이 그래서 chat 예산을 어긴
것도 아니고, 45 ms인 패널이 그 인스턴스의 모든 클래스에 대해 예산을 지킨 것도 아니다.

### 그려진 값 (EXP-109 반복 1, 상주 비중 %와 TBT ms)

| arm | 인스턴스 | chat | dr | agent | TBT p50 | TBT p90 |
|---|---|---|---|---|---|---|
| vLLM router | 1~4 | 47~69 | 15~25 | 8~38 | 97.9 / 105.6 / **398.5** / 133.2 | 212 / 137 / **622** / 286 |
| PolyServe | 1 | 93.1 | 2.6 | 4.4 | 60.2 | 67.3 |
| PolyServe | 2 | 76.2 | 17.4 | 6.4 | 53.5 | 65.5 |
| PolyServe | 3 | 0.0 | 11.6 | 88.4 | 49.4 | 51.7 |
| PolyServe | 4 | 0.0 | 100.0 | 0.0 | 47.9 | 49.8 |
| Llumnix SLO | 1~4 | 27~38 | 55~68 | 5~11 | 71.6~72.9 | 77.3~82.5 |
| llm-d | 1~4 | 34~50 | 38~57 | 10~13 | 39.3~42.4 | 44.9~72.7 |
| FluidServe | 1 | 86.6 | 9.2 | 4.3 | 44.0 | 45.1 |
| FluidServe | 2 | 48.8 | 34.2 | 17.0 | 44.8 | 74.1 |
| FluidServe | 3 | 19.2 | 60.6 | 20.2 | 70.2 | 74.4 |
| FluidServe | 4 | 7.7 | 72.9 | 19.4 | 71.3 | 74.8 |

(120초 평활 기준. 60초로 재면 p50이 0.5 ms 안에서 같고 p90만 PolyServe·Llumnix SLO에서
2~5 ms 높다.)

**기준선 셋만 남긴 판에서 읽히는 것**: **PolyServe는 클래스를 완전히 나누고 속도가 그 순서로
갈린다**(chat 93%인 인스턴스 60.2 ms, agent 88%인 인스턴스 49.4 ms, deepresearch 전용
47.9 ms). **Llumnix SLO는 네 인스턴스가 거의 같은 구성이고 속도도 71.6~72.9 ms로 같다.**
llm-d는 구성이 섞여 있는데 속도가 39~42 ms로 셋 중 가장 빠르고 평평한데, **같은 시간에 훨씬
많이 거절한 결과이기도 하므로**(EXP-109 기준 42.0%) 이 패널만으로 읽으면 안 된다.

**다섯 arm 판에서 읽히는 것**: FluidServe에서 **인스턴스마다 속도가 갈리고 그 방향이 그 인스턴스가 든 클래스와
같다**(chat 86.6%인 인스턴스 44.2 ms, deepresearch 72.9%인 인스턴스 71.4 ms). Llumnix SLO는
네 인스턴스의 구성이 거의 같고 속도도 71~73 ms로 같다. **이것은 상관이지 인과의 측정이 아니다** —
어느 클래스를 어디에 놓느냐와 그 인스턴스가 얼마나 붐비느냐가 같이 움직인다.

---

## `class_mix_hour_3arms_soft.pdf` — 기준선 셋만, 한 열, 채도를 낮춘 판 (2026-09-10)

**스크립트**: 같은 `fig_class_mix_hour.py`.
**크기**: 3.335 × 2.60 in, 단일 열, `width=\columnwidth`. 값은 `class_mix_hour_3arms_soft.csv`.

```
python3 paper_figures/fig_class_mix_hour.py --col --muted \
        --arms slot75 llmdslot75 polyservept75 --suffix _3arms_soft
```

열 셋(**Llumnix SLO / llm-d / PolyServe**)에 행 넷, 축 글씨는 6 pt다(한 열에 세 패널이면
패널 하나가 약 0.85 in이다). `--muted`가 세 클래스를 **같은 색상(hue)의 채도를 낮춘 판**으로
바꾼다 — chat `#d7a791`, deep research `#e3dcba`, agent `#afc1cf`(2026-09-10에 한 번 더
낮췄다. 첫 판은 `#df9675` / `#e3d9a6` / `#9ebbce`였다). RdYlBu 판에서 채도만 내리고 흰색 쪽으로
조금 올린 것이라 **어느 색이 어느 클래스인지가 판 사이에서 바뀌지 않는다**(deep research만
예외다 — `#ffffbf`를 그냥 탈색하면 흰 종이에서 안 보여서 모래색으로 어둡게 했다).

⚠ **더 낮추지 못하는 이유는 종이와의 대비가 아니라 띠와 띠 사이의 경계다.** stacked area에
테두리를 안 그리므로 chat이 끝나고 deep research가 시작하는 자리는 색이 바뀌는 것 하나로만
표시된다. 회색조 밝기가 179 / 218 / 189라 보통 보이는 두 경계(chat|deep, deep|agent)는 39와
29 떨어져 있다.

⚠ **회색조 인쇄에서는 chat과 agent가 안 갈린다**(밝기 179 대 189). 둘은 deep research가 없는
구간에서 실제로 맞닿는다. 채도가 높은 판도 같은 문제이므로(168 대 181) 어느 쪽도 회색조로
안전하지 않고, 캡션이 그렇게 주장하면 안 된다.

⚠ **`class_mix_hour_3arms.pdf`(채도가 높은 판)는 지우지 않고 그대로 두었다.** 두 파일이
같은 데이터의 같은 그림이고 색만 다르므로, **논문에 넣을 때 둘 중 하나만 쓴다.**

---

## `class_mix_hour_split.pdf` — 같은 모자이크에, 제때 낸 일과 늦게 낸 일을 갈라서

**스크립트**: `fig_class_mix_hour_split.py`. **값**: `class_mix_hour_split.csv`.
**크기**: 7.0 × 3.10 in, `figure*`에 `width=\textwidth`. 표는
`build_class_mix_tables.py --split`이 만든다(`hour_engine_mix_split.csv`).

`class_mix_hour.pdf`와 행·열·정렬이 같고, **각 클래스 띠가 둘로 갈린다**: 채워진 부분은 자기
deadline을 지킨 토큰을 낸 일, **빗금 친 부분은 같은 클래스의 같은 인스턴스 위 일인데 늦은 것**이다.
그래서 열 하나를 보면 **어느 클래스가 어느 인스턴스를 함께 썼는가**와 **그 인스턴스가 무엇을
실패했는가**가 같이 읽힌다.

**나누는 방법**: 요청마다 자기 토큰 채점 결과의 비율로 상주 시간을 나눈다 — 토큰이 전부 제때면
전부 채워진 쪽, 3분의 1이 늦으면 3분의 1이 빗금, 토큰을 하나도 못 낸 요청은 전부 빗금이다.
규칙의 주인은 `deadline_ladder_attainment.py`이고 이 스크립트는 그 verdict를 읽는다.

### 늦은 일이 차지한 인스턴스 시간의 비율

| arm | 전체 | chat | deep research | agent |
|---|---|---|---|---|
| **FluidServe** | **0.04%** | 0.0 | 0.0 | 0.2 |
| llm-d | 1.55% | 1.3 | 0.6 | 6.4 |
| **PolyServe** | **24.4%** | **37.1** | 0.1 | 0.3 |
| Llumnix SLO | 37.8% | 49.4 | 28.4 | 59.8 |
| vLLM router | 99.1% | 98.8 | 99.1 | 99.8 |

**PolyServe의 낭비는 한 클래스에 몰려 있다** — chat 서버(Instance 1·2)에서 chat 상주의 40.4%와
31.3%가 늦었고, 파티션이 agent에 잡아 둔 Instance 3은 거의 놀면서 0.2%다. **고정 파티션의 손실이
"어느 인스턴스에서 어느 클래스에" 났는지가 그림에서 바로 보인다.** Llumnix SLO는 네 인스턴스·세
클래스에 고르게 퍼져 있다(섞으면 손실도 섞인다).

### ⚠ 거절은 이 그림에 없다

거절된 요청은 인스턴스에 도달하지 않으므로, **일을 거절하는 것과 받아 놓고 늦게 내는 것이 전혀
다르게 보인다** — 뒤의 것은 빗금이고 앞의 것은 아예 없다. 이 run의 거절률은 FluidServe 19.9 /
PolyServe 22.5 / Llumnix SLO 39.4 / llm-d 49.0 / vLLM router 0.0%다. **llm-d가 깨끗해 보이는
것은 도착의 절반을 받지 않았기 때문이고, 캡션이 이것을 말하지 않으면 그 열은 오독된다.**

### ⚠ 빗금은 비율이지 시각이 아니다

한 요청의 늦은 토큰이 그 요청의 생애 뒤쪽에서 나왔다는 보장이 없으므로, 어떤 창의 빗금은
**그 인스턴스 점유 중 늦은 일이 차지한 몫**이지 **늦음이 일어난 분**이 아니다.

vLLM router 열은 `class_mix_hour.pdf`와 같은 이유로 admitted의 74.9% 위에 그려진다.

## `decode_kv_qwen_{vllm,fs}.pdf` — decode가 만드는 KV와 토큰당 시간, Qwen 한 시간

**스크립트**: `fig_decode_kv_qwen.py` (`--arm vllm fs`, 기본값은 둘 다).
**값**: arm마다 `decode_kv_qwen_<arm>.csv`.
**크기**: **3.335 × 1.72 in** (한 칼럼, `azure_rate_and_mix.pdf`와 같은 폭·같은 8 pt),
`_wide`는 7.0 × 1.72 in. `width=\columnwidth`로 넣으면 배율 1.0이다.
⚠ **65% 캔버스(2.168 × 1.27 in)를 한 번 만들었다가 되돌렸다** — 폭을 줄이면 8 pt 활자가 캔버스의
대부분을 차지해 그려지는 축 영역이 1.4 × 0.75 in로 떨어지고 오른쪽 축 이름이 축 옆에 서지 못한다.
**작게 만들 때는 높이를 줄인다**(1.95 → 1.72 in).
탐색용 원본은 `results/aggregate_analysis/decode_kv_growth/decode_vs_itl.png`(두 모델 나란히,
한국어 라벨, 61분까지, FluidServe)이고 이 그림은 **Qwen 쪽만 0~60분으로 잘라 논문 형식으로 다시
그린 것**이다. 계산은 `analysis_scripts/request_level/decode_kv_growth.py`의 함수를 import해서
쓰므로 값이 갈리지 않는다.

⚠ **arm 이름이 파일 이름에 들어간다.** 같은 그림이 어느 정책의 run인지에 따라 전혀 다른 것을
말하기 때문이다(아래 표). 이름 없는 옛 파일 `decode_kv_qwen.pdf`·`_wide.pdf`·`.csv`는
`_fs` 판과 같은 내용이고 **더 이상 갱신되지 않는다.**

- **왼쪽 축(파랑) `KV-cache (GB/s)`**: decode가 KV를 만들어내는 속도, 엔진 넷 합.
  범례 이름은 `KV-cache Generation by Decode`.
- **오른쪽 축(빨강) `TBT (ms)`**: 엔진이 스스로 보고하는 토큰간 시간, 토큰 가중 평균.
  범례 이름은 `Time between Token (TBT)`.
  ⚠ **축 이름은 짧은 형태이고, 그것은 재서 고른 것이다** — `Time between Token, TBT (ms)`를 8 pt로
  세우면 약 1.05 in인데 축 높이가 그보다 작아 잘린다. 스크립트가 **레이아웃을 끝낸 뒤** 라벨의
  렌더링 크기를 축 높이와 비교해 긴 형태·짧은 형태 중 들어가는 것을 고르고 어느 쪽을 썼는지 출력한다.
  **회전한 라벨의 길이는 extent의 height다**(width는 한 줄의 두께다) — 이것을 반대로 보면 어떤
  라벨도 "들어간다"고 판정되어 캔버스 밖으로 그려진다.
  **긴 이름은 범례가 한 번 말한다.**
- x축은 `Time (minutes)`.
- **범례는 한 줄**이고 글자 크기는 **재서 고른다** — 두 이름을 한 줄에 놓고 렌더링 폭이 캔버스의
  95% 안에 드는 가장 큰 크기를 쓴다(한 칼럼 판에서 6.5 pt, 전체 폭 판에서 8 pt). matplotlib은 한 줄
  범례가 캔버스를 넘어가도 아무 말을 하지 않으므로 **맞는지는 재야 안다.**
- **반복은 하나만 그린다**(반복 1). 두 반복이 겹쳐서 — FluidServe의 한 시간 총량이 14,544 대
  14,515 GB로 0.2% 차이다 — 점선을 하나 더 그으면 구별되지 않는 곡선만 늘어난다.

### 두 arm이 같은 한 시간에서 서로 다른 것을 말한다

| | 만든 KV | 생성 속도 p10 / p90 | 토큰간 시간 p10 / p50 / p90 |
|---|---|---|---|
| **FluidServe** | **14,544 GB** | 1.97 / 5.21 GB/s (2.6배) | **34 / 47 / 52 ms** |
| **vLLM router** | **8,043 GB** | 1.10 / 3.69 GB/s (3.4배) | **24 / 90 / 177 ms** (최대 229) |

**vLLM router 쪽에서 두 곡선이 갈라지는 것이 이 그림의 내용이다**: 10분 무렵 5.8 GB/s까지 올라간
생성 속도가 뒤로 갈수록 1.2 GB/s 근처로 내려가고, 그 사이 토큰간 시간이 20 ms에서 180~230 ms로
올라간다. 거절을 하지 않아 상주 요청이 쌓이고, **엔진이 같은 시간에 더 적은 토큰을 만든다.**
FluidServe는 같은 한 시간에 **1.8배의 KV를 만들면서** 토큰간 시간을 50 ms 근처에 유지한다.

⚠ 두 그림은 **각자의 오른쪽 축**을 쓴다(FluidServe는 63 ms까지, vLLM은 229 ms까지). 나란히 놓으려면
`--itl-top`으로 축을 고정한다. 고정하지 않고 나란히 놓으면 두 빨강 곡선의 높이가 비교 가능한 것처럼
보인다.

### ⚠ 이것은 생성 속도이지 점유량이 아니다

요청이 끝나면 자기 몫을 통째로 반납하므로 **곡선 아래 면적은 한 시간 동안 만들어졌다가 사라진
양**이지 쌓이는 수위가 아니다(물리 풀은 세 자릿수 작다). **"decode가 초당 X GB의 KV를 만든다"로
쓰고 "X TB가 쌓인다"로 쓰지 않는다.**

### 토큰당 KV 크기는 이제 가정이 아니다

`2 × layers × KV heads × head dim × dtype bytes`. 원본 스크립트는 Qwen을 Llama와 같은 모양으로
**가정**하고 "논문에 넣기 전에 확인하라"는 경고를 달고 있었다. 엔진이 실제로 읽는
`config.json`을 확인했다(`models--Qwen--Qwen2.5-72B-Instruct`: layers 80, attention heads 64,
**KV heads 8**, hidden 8192 → head dim 128, bfloat16). **327,680 B = 320 KiB/token으로
Llama-3.1-70B와 같다** — 두 모델의 곡선이 같은 단위이므로 나란히 놓아도 된다.

### ⚠ Qwen trace는 솎아낸 것이다

이 한 시간은 Llama 쪽 도착의 **64%**만 재생한다(모델이 이 하드웨어에서 느려서). 원본 PNG의 Llama
패널(13,851 GB, 토큰간 시간 p10/p90 = 37/67 ms, FluidServe)과 나란히 인용할 때는 **부하가 다르다는
것을 같이 적는다.**

### 데이터를 어떻게 가공하는가 (여섯 단계)

1. 엔진마다 `server_metrics/engine_*.jsonl`이 **스크레이프 한 줄**씩을 담고 `t`가 그 시각이다.
2. 엔진별 **누적** `vllm:generation_tokens_total`을 **1초 격자에 보간**한 뒤 네 엔진을 더한다.
   네 엔진의 스크레이프 시각이 서로 다르므로 **더하기 전에 보간한다.**
3. 인접한 초의 차분이 초당 토큰이다. **음수는 0으로 자른다** — 엔진이 재기동하면 카운터가
   내려가고, 그것을 큰 음의 속도로 읽으면 안 된다.
4. 토큰당 KV 크기를 곱하고 1e9로 나누면 GB/s다.
5. **20초 box filter**로 평활한다(원본과 같은 필터라 두 그림을 비교할 수 있다).
6. x축은 **첫 스크레이프가 아니라 첫 도착**에서 잰다(0분 = 부하 시작). 0~60분만 남긴다.

토큰간 시간은 같은 스크레이프에서 만든다: 엔진별로
`inter_token_latency_seconds_sum`과 `_count`의 초당 증분을 구해 **네 엔진의 증분을 각각 합한 뒤
비를 취한다**. 이것이 토큰 가중 평균이라 **실제로 토큰을 만드는 엔진을 따라간다** — 네 엔진의 평균을
다시 평균하면 한가한 엔진의 몇 토큰이 바쁜 엔진의 수만 토큰과 같은 무게를 갖는다.
**한 시간 총량은 평활한 곡선을 적분하지 않고 카운터의 양 끝 차이로 낸다** — 평활이 총량을 움직이면
안 되기 때문이다.

### 캡션

> **Figure N: What decoding creates, and what it costs per token (Qwen2.5-72B, one hour).**
> The blue line is the rate at which decoding brings KV cache into existence, summed over the
> four engines; the area under it is the volume created and released over the hour, not a level
> that accumulates. The red line is the inter-token latency the engines report, token-weighted
> across them.

### 데이터

EXP-113(2026-09-03) 반복 1 — vLLM router는 `260903_0702_exp113r1_vllmcachet75_shiftq`,
FluidServe는 `260903_0302_exp113r1_fsv3capgnofrct75_shiftq`. 같은 이름의 다른 디렉토리 다섯은
부분 run이라 `excluded_runs.tsv`에 있다. 20초 box filter, 시간의 0은 첫 도착이다.

## `engine_window_<arm>_<port>.pdf` — 한 인스턴스, 10분, 네 가지를 한 장에

**스크립트**: `fig_engine_window.py`
(`--arm {slo,polyserve,fluidserve,llmd,vllm} --port 8000 --t-lo 33 --t-hi 43`).
**값**: 같은 basename의 `.csv`. **크기**: 3.335 × 2.55 in / `_wide` 7.0 × 2.10 in.

**(a)** 그 엔진의 KV 점유율과 running batch, **(b)** 그 엔진이 보고한 토큰간 시간과 **그 엔진에
배치된 요청들의 SLO 달성률(admitted)**. x축을 공유한다. 기본 구간은 EXP-109 반복 1의 **33~43분**
(첫 도착 기준)이고, 다섯 arm 모두 귀속률이 99.9~100%다(vLLM router만 74.9%).

⚠ **파일 이름에 arm과 포트가 들어간다.** 같은 그림이 arm과 엔진에 따라 완전히 다른 것을 말하고,
**포트 번호는 arm 사이에서 같은 것을 뜻하지 않는다** — PolyServe는 8000·8001이 chat, 8002가 agent,
8003이 deep research인 반면 Llumnix SLO는 섞으므로 네 엔진이 부하로만 다르다. 옛 이름
`engine8002_window.*`는 `engine_window_slo_8002.*`로 대체됐다.

### 지금 그려 둔 셋 (33~43분)

| 그림 | KV p50 | batch p50 | TBT p50 | SLO(admitted) 평균 | 요청 |
|---|---|---|---|---|---|
| `polyserve_8000` (chat 전용) | 57.8% | **376** | 61.8 ms | **53.5%** | 20,966 |
| `polyserve_8002` (agent 전용) | 40.0% | **77** | 44.4 ms | **98.5%** | 4,359 |
| `slo_8002` (섞임) | **90.3%** | 234 | **73.8 ms** | **49.7%** | 11,279 |

**같은 함대, 같은 10분이다.** PolyServe의 chat 서버는 running batch가 376(최대 429)인데 KV는
57.8%밖에 안 차고, 토큰간 시간 61.8 ms로 배치된 요청의 절반을 놓친다. 같은 시각 그 arm의 agent
서버는 batch 77로 98.5%를 지킨다 — **파티션이 한쪽에 일을 몰아 두고 다른 쪽을 비워 둔 상태가 그대로
보인다.** Llumnix SLO는 섞기 때문에 그 엔진 하나가 KV 90%로 차서 73.8 ms를 낸다.

### ⚠ 게이지는 평활하지 않는다

KV와 running batch는 **1초 스크레이프 그대로** 그린다(10초 box filter를 쓰다가 뺐다 — 전이의
모서리를 깎았다). **토큰간 시간만 3초 필터**를 유지하는데, 그것은 초당 증분 **둘의 비**여서 그 초에
끝난 토큰이 적으면 지연이 변하지 않아도 비가 크게 움직이기 때문이다. 나눗셈 **전에** 분자와 분모를
각각 평활한다.

### ⚠ TBT 축은 99분위에서 자르고, 넘어간 초는 위 모서리에 찍는다

평활을 빼면 나머지의 서너 배인 초가 몇 개 나온다(PolyServe 8000에서 4초, 최대 367 ms;
Llumnix SLO 8002에서 6초, 최대 379 ms). 축을 거기까지 늘리면 패널의 대부분을 그 몇 초에 쓰고
읽어야 하는 40~110 ms 띠가 납작해진다. **삼각형으로 표시하고 개수와 최댓값을 패널에 적으며,
CSV에는 모든 값이 있다.**

### ⚠ 달성률 창은 run 전체에서 잘라 구간만 crop한다

90초 창(30초 간격)을 **run 전체에 대해** 만든 뒤 중심이 구간 안에 드는 것만 그린다. 구간 안에서만
창을 만들면 양끝이 45초씩 비는데, 그 창들은 실제로 존재한다. 요청은 **도착한 창**에 들어가므로
미스가 "늦었다고 판정된 순간"이 아니라 "도착한 순간"에 나타난다 — 두 패널은 분 단위로 읽는다.

### ⚠ admitted라서 거절이 안 보인다

이 run의 거절률은 PolyServe 22.5%, Llumnix SLO 39.4%다. 거절된 요청은 엔진에 도달하지 않아 두
패널 어디에도 없다. 그래서 (b)의 상승은 **엔진이 더 잘 처리한 것**일 수도 **제어평면이 실패할
요청을 덜 보낸 것**일 수도 있다.

### 예산선을 굳이 긋지 않은 이유

엔진에 여러 클래스가 올라가고 토큰당 예산이 chat 50 / deepresearch 100 / swe 75 ms로 다르다.
한 줄을 그으면 그 줄이 모든 요청의 기준인 것처럼 읽힌다. 필요하면 캡션에서 말한다.
(PolyServe 8000처럼 chat이 97.6%인 엔진은 예외로 50 ms 선을 그어도 오독되지 않는다.)

### 캡션 (PolyServe 8000)

> **Figure N: One instance under PolyServe, minutes 33-43 of the hour.**
> This is one of the two servers its partition assigns to the chat class; 97.6% of the requests
> resident on it in this window are chat. (a) the KV pool it held and the requests it was
> running, as scraped each second; (b) the inter-token latency it reported and the share of the
> requests dispatched to it that met their deadline, on the admitted denominator, in 90 s
> windows placed at each request's arrival. The running batch reaches 429 requests while the KV
> pool stays near 58%, and just over half the work it is given misses its deadline.

### 축을 자른 판과 peak 정규화 판 (2026-09-08)

같은 스크립트가 플래그로 낸다. 이름에 접미사가 붙는다.

```bash
python3 paper_figures/fig_engine_window.py --arm slo --port 8002 \
        --kv-floor 30 --batch-floor 50 --suffix _zoom
python3 paper_figures/fig_engine_window.py --arm slo --port 8002 \
        --normalize --suffix _norm
```

- `engine_window_slo_8002_zoom.pdf` — KV 축이 30%에서, batch 축이 50에서 시작한다.
- `engine_window_slo_8002_norm.pdf` — (a)의 두 계열을 **각자의 구간 내 최댓값으로 나눠** 한 축에
  올린다(0~100% of peak). 나눈 값(KV 100%, batch 392)을 패널에 적는다 — 안 적으면 원래 양으로
  되돌아갈 수 없다.

⚠ **둘 다 계열을 실제보다 변동이 큰 것처럼 보이게 한다.** 30%에서 시작하는 축은 47~91의 변화를
바닥에 닿는 곡선으로 만들고, peak 정규화는 서로 비교할 수 없는 두 양을 같은 0~100으로 겹쳐
놓는다. **기본 판(0에서 시작, 정규화 없음)이 기본이고**, 두 곡선의 **모양**을 비교하는 것이 논점일
때만 이 판들을 쓰고 캡션에 어느 판인지 적는다.

### 왜 달성률만 덜 흔들리나 (2026-09-08 측정)

엔진 게이지는 1초 표본이고 달성률은 **90초 창을 30초마다** 낸 것이라, 이웃한 두 점이 요청의
3분의 2를 공유한다. 창을 좁히면 그만큼 흔들린다 — 같은 run·같은 엔진·같은 10분에서:

| 창 / 간격 | 점 | 평균 | 표준편차 | 최소~최대 | 창당 요청 | 이항 표준오차 |
|---|---|---|---|---|---|---|
| 90 s / 30 s | 20 | 45.3% | **14.3** | 21.5~78.6 | 581 | 2.07 |
| 30 s / 30 s | 20 | 46.7% | **20.6** | 19.7~89.0 | 195 | 3.57 |
| 15 s / 15 s | 40 | 48.9% | **24.8** | 13.3~100.0 | 98 | 5.06 |

**표본 잡음이 작아서 매끄러운 것이 아니다** — 창당 581건이면 이항 표준오차가 2.1점인데 실제 표준
편차는 14.3점이다. 매끄러움은 두 가지에서 온다: **창의 겹침**(위 표), 그리고 **양 자체가 저역
통과**라는 것 — 요청 하나의 판정은 그 요청이 사는 동안의 조건 전체를 적분한 값이고, 이 엔진에서
요청의 수명은 **중앙값 37.8초, p90 77.2초**다. **창을 1초로 줄여도 1초짜리 스파이크는 달성률에
나타날 수 없다.** 게다가 규칙이 "토큰의 95% 이상"이라, 300 ms짜리 초 하나는 긴 요청의 토큰 몇 개만
늦게 만들어 판정을 거의 뒤집지 못한다.

### 정규화 두 가지와 30초 창 판 (2026-09-08)

```bash
--normalize peak      # x / max(x)              → _norm
--normalize minmax    # (x-min) / (max-min)     → _norm10
--att-win 30 --att-step 30                      → _att30
```

**두 정규화 모두 그려지는 10분 안에서만 계산한다**(한 시간 전체가 아니다). 차이는 0을 어디에
두느냐다.

| | 스케일 | 이 창에서 | 그래서 |
|---|---|---|---|
| `_norm` (peak) | `x / max` | KV peak 100%, batch peak 392 | **0에서의 거리를 지킨다** — 자기 최댓값의 절반 아래로 안 내려간 계열은 위쪽 절반에 머문다 |
| `_norm10` (min-max) | `(x−min)/(max−min)` | KV **36~100%**, batch **91~392** | **축 전체를 그 계열이 실제로 지나간 범위에 쓴다** — 모양은 가장 잘 보이고, 움직임이 얼마나 컸는지는 완전히 사라진다 |

min-max 판에서 새로 읽히는 것: **35~41분에 KV는 자기 범위의 70~100%에 계속 붙어 있는데 batch는
40~100%를 오간다.** 두 계열이 같이 움직이는 것은 33~35분의 상승과 41~42분의 하강뿐이고, 그 사이는
batch만 움직인다. peak 판에서는 KV가 36%까지 내려간 적이 있다는 사실이 곡선을 아래로 눌러
이 차이가 덜 보인다.

**`_att30`은 달성률 창을 90초/30초에서 30초/30초로 바꾼 판**이다(겹치지 않는다). 같은 10분에서
평균 52.6 / 최소 19.7 / 최대 100.0%, 창당 188건(20창, 3,760 요청). 38분과 39.5~41분의 낮은
구간이 90초 판보다 뚜렷해진다. **다만 요청 하나의 판정은 그 요청 수명(중앙값 37.8초) 전체를 적분한
값이라, 창을 30초로 줄여도 초 단위 스파이크에는 반응하지 않는다** — 창을 좁혀 얻는 것은 잡음이
아니라 겹침을 없앤 만큼의 해상도뿐이다.

`_norm10`은 **15초/15초**로 더 좁혔다(2026-09-08 요청): 평균 54.7 / 최소 13.3 / 최대 100.0%,
**창당 94건**(40창). 이 지점부터는 이항 표준오차가 **5.1점**이라 곡선의 작은 요철에 표본 잡음이
섞이기 시작한다 — 그보다 좁히려면 그 사실을 캡션에 적어야 한다.

`_norm10`에 같이 적용한 것 둘:

- **잘린 TBT 축 위의 삼각형을 뺐다**(`--no-over-markers`). **개수와 최댓값을 적은 글은 남는다** —
  그것까지 빼면 잘린 축이 그 계열의 가장 큰 초들을 말없이 버린다.
- **위 패널을 다시 두 축으로 나눴다**: 왼쪽 `KV-cache (norm.)`, 오른쪽 `Batch (norm.)`, 둘 다
  0~100. 정규화했으므로 **눈금은 같고**, 축 이름이 각 색 옆에서 그 곡선이 무엇인지 말한다.

### 좌우 배치 판 `engine_window_slo_8002_norm10_side.pdf` (2026-09-08)

```bash
python3 paper_figures/fig_engine_window.py --arm slo --port 8002 \
        --t-lo 34 --t-hi 39 --normalize minmax --att-win 15 --att-step 15 \
        --no-over-markers --no-notes --side --suffix _norm10_side
```

7.0 × 1.62 in, `figure*`에 `width=\textwidth`. **두 패널을 한 줄에** 놓고 구간을 **34~39분**으로
좁혔다. `--side`는 **전체 폭 판만** 쓴다 — 한 칼럼에 두 패널이면 각 1.4 in인데 거기에 y축이 둘씩
붙어 읽을 수 없다. 좌우로 놓으면 왼쪽 패널의 **오른쪽** 축 이름과 오른쪽 패널의 **왼쪽** 축 이름이
가운데서 만나므로 `w_pad`를 2.6으로 벌린다.

⚠ **패널 안 주석을 다 뺐으므로(`--no-notes`, `--no-over-markers`) 캡션이 그것을 져야 한다.**
스크립트가 매번 찍어 준다:

- (a)의 스케일: **창 min-max, KV 36~100%, batch 101~373**
- (b)의 축: **160 ms에서 자름, 위로 넘어간 초 3개, 최대 232 ms**
- (b)의 달성률: 15초 창 20개, 1,830 요청, 최소 16.6 / 평균 50.9 / 최대 100.0%

### 캡션

> **Figure N: Dynamically varying instance state.** One instance under Llumnix SLO, over five
> minutes of the hour-long trace whose arrival rate comes from an Azure production trace.
> (a) the KV pool it held and the requests it was running, each scaled over the window shown;
> (b) the time per token it reported and the share of the requests it was given that met their
> deadline, on the admitted denominator. Every quantity moves within these five minutes, and
> they do not move together.

---

## `slo_scale.pdf` — 세 함대에서 SLO 배율에 대한 offered 달성률 (2026-09-10)

```bash
python3 paper_figures/fig_slo_scale.py                # slo_scale.pdf + .csv
python3 paper_figures/fig_slo_scale.py --scale-ttft   # slo_scale_allbudgets.pdf + .csv
```

7.0 × 2.05 in, `figure*`에 `width=\textwidth`. **전체 폭을 쓴 이유**: 패널이 셋이다. 한 칼럼
(3.335 in)에 셋을 넣으면 패널 하나가 약 1.0 in이 되어 x축 눈금 여섯 개가 서로 겹친다.

**무엇을 주장하는 그림인가.** 가장 강한 기준선에 대한 순위가 **채점 규칙에 견고한 셀이 있고
아닌 셀이 있다**는 것, 그리고 그 차이 자체가 결과라는 것. 아래 수치는 전부 **그 배율에서 가장
높은 기준선과의 차이**(offered, 2반복 평균)다.

- **4×Qwen2.5-72B — 견고하다.** 배율 0.9~3.0 전 구간에서 FluidServe가 앞서고 차이가
  **+11.9(k=0.9) ~ +27.0(k=1.0), 배율 1.2 위로는 +20.3에서 평평하다.** PolyServe만 놓고 보면
  +20.3~+28.4다. PolyServe가 38.1%를 거절해 천장이 61.9이고, 예산을 아무리 풀어도 그 위로
  못 간다.
- **4×Llama-3.1-70B — 견고하지 않다.** 배율 1.0에서 **+27.5**(2위 llm-d 50.8 대비; PolyServe
  대비로는 +47.2)이던 우위가 배율 1.3에서 +19.2, **배율 1.5에서 +0.8로 사라진다.** 그
  지점에서 두 arm의 천장이 79.7과 79.8로 사실상 같고, **PolyServe의 반복 간 폭이 76.5 대
  81.3으로 4.8점**이라 +0.8은 읽을 수 있는 차이가 아니다(CLAUDE.md: 평균 차이가 편차보다
  작으면 "차이 없음").
- **8×Llama-3.1-8B — 배율로 구할 수 없다.** 배율 0.6~3.0 어디에서도 PolyServe가 앞서고,
  차이는 배율 1.0에서 −8.0, 1.5 위로 −6.4에서 평평하다.

### 데이터

| 패널 | run 디렉토리 | 반복 |
|---|---|---|
| (a) 4×Llama-3.1-70B TP=2 | `260831_2015`·`260901_1514` fsv3capgnofrct75, `260831_2232`·`260901_1742` polyservept75, `260831_2128`·`260901_1637` llmdslot75, `260831_2346`·`260901_1856` slot75 (전부 `_shift`) | arm당 2 |
| (b) 4×Qwen2.5-72B TP=2 | `260903_0302`·`260903_1031` fsv3capgnofrct75, `260903_0436`·`260903_1139` polyservept75, `260903_0834`·`260903_1537` llmdslot75, `260903_0548`·`260903_1252` slot75 (전부 `_shiftq`) | arm당 2 |
| (c) 8×Llama-3.1-8B TP=1 | `260908_2055_exp114h62r1_fsv3capgnofrct75_shift62`, `260909_0900_exp114mlr1_fsv3capgnofrct75_shift62`, `260908_2317_exp114h62r1_polyservept75_shift62` | FluidServe 2, **PolyServe 1** |

**세션**: (a) EXP-109 2026-09-01/02, (b) EXP-113 2026-09-03, (c) EXP-114 2026-09-08/09.
세 패널은 서로 다른 함대이므로 **패널을 가로질러 값을 빼지 않는다** — 같은 trace이지만 8B는
도착률이 ×6.20이고 예산의 정규화된 난이도가 다르다(EXP-117 §7.6: 부하 중 토큰당 시간으로
재면 8×8B에서 예산이 약 2배 느슨하다).

⚠ **(c)의 두 FluidServe 반복은 서로 다른 바이너리에서 돌았다**(`b1c12f37`와 `05c7baa8`).
offered가 68.41 대 67.51로 1.1점 차이이고 그것이 이 셀의 반복 간 폭이다.

⚠ **PolyServe @ 8×8B는 n=1이라 오차막대가 없고, 따라서 이 그림에서 가장 정밀한 점처럼
읽힌다.** CSV의 `n_repeats`만이 그것을 말한다. 캡션이 져야 한다.

### 그리지 않은 arm — vLLM router

거절을 하나도 하지 않아 backlog이 쌓이고, **도착의 51.7~59.2%가 한 시간이 끝날 때까지
미완료**다. 미완료 요청은 결과가 정해지지 않았으므로 `attain()`이 **두 분모 모두에서 뺀다.**
따라서 그 곡선은 완주한 41~48%(= 빠른 것들)만을 모집단으로 하고, 나머지 arm의 곡선은 각자
도착의 99.4~99.8%를 모집단으로 한다. **모집단이 다른 두 곡선을 한 축에 놓지 않는다** —
`exp109_hour.pdf`의 네 arm 판이 같은 arm을 같은 이유로 뺀다.

재채점 값은 여기 남긴다(offered, per-token 배율만, k = 0.8 / 1.0 / 1.5):
`260901_2137_exp109r1_vllmcachet75_shift` **26.6 / 36.9 / 48.2**,
`260901_2010_exp109r2_vllmcachet75_shift` 26.6 / 37.7 / 48.9,
`260903_0702_exp113r1_vllmcachet75_shiftq` 33.5 / 53.0 / 55.5,
`260903_1406_exp113r2_vllmcachet75_shiftq` 35.0 / 54.1 / 58.3.

⚠ **반복 1의 run 선택이 갈린다.** `results/*exp109r1_vllmcachet75_shift`는 다섯 디렉토리가
매칭되고 그중 셋이 병합·90분 조건을 통과한다(`0645`·`1011`·`2137`). `pick_usable_run.sh`와
EXP-109 §3의 표는 **`2137`**(k=1.0에서 36.9)를 쓰고, EXP-117 §8.2의 표는 **`1011`**(39.3)을
쓴다. 위 값은 `pick_usable_run.sh`의 선택이다.

### 지표 정의

- **offered 달성률**: 분석 창(첫 도착 +60초 ~ 마지막 도착 −20초) 안에 도착한 모든 요청이
  분모이고, 거절·오류·무응답은 위반이다. 미완료(run 경계 절단)는 결과가 미정이므로 두 분모
  모두에서 빠진다. `exp22_fluidserve.per_request(rows, "violate_offered")`.
- **배율 k**: **토큰당 예산에만** 곱한다(chat 50, deepresearch 100, swe 75 ms/token).
  첫토큰 예산 5 / 10 / 7초는 고정이다. 이유 둘 — 첫토큰 예산은 상호작용의 성질이지 토큰
  속도의 성질이 아니고, 짝이 되는 `crossover_footprint.pdf`가 **토큰당 천장 하나에 대한**
  주장이라 둘을 같이 움직이면 두 그림이 다른 질문에 답하게 된다.
- **swe는 per-token 형태로 채점한다**(`FS_SWE_TBT_MS=75`, `FS_SWE_TTFT_S=7`). e2e 30초로
  채점하면 배율이 작용할 토큰당 항이 없어 그 열만 평평해진다.
- **천장**(`offered_ceiling_pct` = 100 − 거절률 − 오류율): 그림에서 오른쪽 여백의 짧은 점선.
  모든 정책이 예산을 **입력으로** 받으므로 거절은 배포 시점 예산에 고정돼 있고, offered
  달성률은 어떤 k에서도 이 값을 넘을 수 없다.

### 재채점이 답하지 않는 것 — 캡션에 반드시 들어가야 한다

재채점은 **"순위가 채점 규칙에 견고한가"**에 답한다. **"그 예산이었으면 정책이 다르게
굴었을까"**에는 답하지 않는다. 곡선이 80에서 평평해지는 것은 채점의 한계가 아니라 그 arm
자신의 거절률이 만든 천장이다.

### EXP-117 §8.2의 표와 어긋나는 곳, 그리고 그 이유

그 표는 **첫토큰 예산도 k배 했다.** `--scale-ttft`가 그것을 그대로 재현하고
`slo_scale_allbudgets.pdf` / `.csv`로 나온다. **k = 1.0에서는 두 판이 소수점 끝까지
일치하고**(최대 |차이| 0.0), 멀어질수록 갈린다:

| 범위 | 차이 (모든 예산 배율 − 토큰당만) |
|---|---|
| k ≤ 1.5 | **+3.3 ~ −2.8점** (최대: 8×8B FluidServe @ k=1.5, 69.7 → 73.0) |
| k ≤ 3.0 | **+16.6점**까지 (4×70B Llumnix SLO @ k=3, 43.5 → 60.2) |

첫토큰 예산을 늘리면 **토큰당 시간이 애초에 문제가 아니었던 요청**이 살아나기 때문이다.
FluidServe의 4×70B 열은 두 판이 거의 같은데(첫토큰이 예산 안에 넉넉히 들어 있다), Llumnix
SLO와 8×8B의 FluidServe는 크게 움직인다(EXP-117 §7.4: 수용된 swe의 47.65%가 7초 TTFT를
넘고 토큰당 75 ms를 넘는 것은 0.07%다).

### 캡션

> **Figure N: SLO attainment against the SLO scale.** Each panel re-scores the same
> hour-long mix-shift trace at a multiplier k on the per-token budgets, with the
> first-token budgets held fixed. Attainment is per request on the offered denominator:
> every arrival counts, and a rejected, errored or unanswered request counts as a
> violation. Bands are min-max over two repeats; PolyServe on the eight-instance fleet
> is a single run and has no band. The dotted vertical rule marks k = 1, the deployed
> budget and the only multiplier at which the policies ran. The dotted segments at the
> right of each panel mark each arm's ceiling, 100 minus its rejection and error rates.
> Every policy takes the budget as an input, so the rejections are frozen at the
> deployed value and no multiplier can move a curve past that ceiling. The figure
> therefore answers whether the ranking survives a change of scoring rule; it does not
> answer how a policy would have behaved under a different budget. The vLLM router is
> excluded because it rejects nothing and leaves 52-59% of its arrivals unfinished at
> the end of the hour, which removes them from both denominators.

---

## `crossover_footprint.pdf` — 교차 footprint `T*`, 그리고 실제로 어느 천장이 구속했나 (2026-09-10)

```bash
python3 paper_figures/fig_crossover_footprint.py     # crossover_footprint.pdf + .csv
python3 analysis_scripts/request_level/crossover_footprint.py   # 표만 출력
```

7.0 × 2.15 in, `figure*`에 `width=\textwidth`. **전체 폭을 쓴 이유**: 왼쪽 패널이 로그 y축에
세 곡선 + 세 밴드 + 세 수평선 + 세 클래스 예산선을 담고, 오른쪽 패널이 3×2 막대다. 한 칼럼에
넣으면 왼쪽 패널이 1.9 in가 되어 밴드가 서로 구분되지 않는다.

**무엇을 주장하는 그림인가.** 인스턴스에는 천장이 둘 있다 — **속도 천장**(디코드 step 시간이
토큰당 예산에 닿는 배치)과 **메모리 천장**(KV 풀이 차는 배치). 어느 쪽에 먼저 닿는지는
워크로드의 성질이 아니라 **(모델, 병렬화, KV 풀)의 성질**이고, 프로파일 표와 풀 크기만으로
닫힌 형태로 쓸 수 있다:

```
T* = C·(c_n + ρ/R) / ((B − c0)·s − C·c_kv)
```

`T*`보다 작은 요청당 footprint에서는 속도 천장이 낮고, 크면 메모리 천장이 낮다. 유도는
`analysis_scripts/request_level/crossover_footprint.py`의 docstring에 있다. **같은 trace가
같은 컨트롤플레인 아래에서 함대마다 다른 천장에 닿는다.**

### 입력 — 무엇이 측정이고 무엇이 유도인가

| 양 | 출처 | 값 (70B×4 / Qwen72B×4 / 8B×8) |
|---|---|---|
| `c0`, `c_kv`, `c_n` | `deploy/profiling/<dir>/fluidserve.json`의 `decode_step_law`, **디코드 전용 step에 적합** | c0 16.361 / 18.125 / 4.291 ms |
| `R` | 같은 파일의 `prefill_step_law`, **8192 토큰 앵커**(엔진에 설정된 청크이자 가장 큰 실측점) | 15.76 / 15.34 / 69.81 tok/ms |
| `C` (KV 풀) | 아래 "풀 감사" 참조 | 584,928 (실측) / 576,054 (**유도**) / 1,152,240 (엔진 보고) |
| `s` (물리/논리 공유비) | **run에서 측정**, 부하 창만 | 0.691 / 0.728 / 0.783 |
| `ρ` (생성 토큰당 다시 prefill해야 하는 프롬프트 토큰) | **run에서 측정**, 부하 창만 | 1.994 / 1.258 / 1.761 |
| 실측 요청당 footprint (p50) | **run에서 측정**, 부하 창만 | 4,347 / 5,023 / 4,001 토큰 |
| **`T*` (chat 50 ms)** | 위 값들로 계산 | **7,541 / 5,141 / 2,323** |

`s`는 엔진의 `vllm:kv_cache_usage_perc`(물리 점유율)를 스케줄러의
`scheduler_fluidserve_obs_kv_tokens`(논리 KV)와 **스크레이프마다 짝지어** 나눈 값이다.
`ρ`는 `vllm:prompt_tokens_total`(창 안 증분)을 `vllm:generation_tokens_total`로 나눈 뒤
prefix cache hit rate를 뺀 것이다 — 그 카운터는 프롬프트 **전체**를 세므로(클라이언트가
보낸 input 토큰 합과 2% 안에서 일치한다) 캐시에 맞은 몫을 빼야 실제로 다시 계산된 토큰이
남는다. **모든 비는 짝지어 만들었고 중앙값끼리 나눈 것이 하나도 없다**(CLAUDE.md 함정 E).

**부하 창으로 자르는 것이 필수다.** 수집기는 부하가 시작되기 전부터 드레인 뒤까지 긁으므로
run 전 구간의 엔진 게이지 분위수는 절반이 유휴다.

### 풀 감사 — 어느 KV 풀 값을 쓸 것인가

vLLM은 GPU당 예산에서 가중치와 자기 오버헤드를 뺀 나머지로 KV 풀을 잡으므로, **같은
하드웨어에서는 오버헤드 상수 하나가 모든 함대를 설명해야 한다.** GPU당 예산은
183,359 MiB(`nvidia-smi`) × 0.9(`--gpu-memory-utilization`, `deploy/neutral/full-mode-scheduling/`)
= **173.04 GB**이고, 가중치는 bf16 파라미터 수를 병렬화로 나눈 값이다.

| 8B 풀로 보정한 오버헤드 | 그 오버헤드가 예측하는 70B 풀 | 실측 584,928 대비 |
|---|---|---|
| 엔진 보고 **1,152,240** → **5.952 GB/GPU** | 589,192 | **+0.73%** |
| 스케줄러 `kvCapacity` **1,037,648** → **20.972 GB/GPU** | 497,518 | **−14.94%** |

→ **엔진 값을 쓴다.** Qwen2.5-72B 풀 **576,054 토큰**은 같은 오버헤드로 유도한 값이고
**측정된 적이 없다** — Qwen 셀의 모든 수치가 여기 걸려 있다.

⚠ **새로 나온 것 하나**: 8×8B run 자신의 데이터로 스케줄러의 `kvCapacity`를 복원할 수 있다.
`fluidserve.go:1310`이 `capMem = kvCapacity × 0.95 / ratio`로 쓰고 `ratio`가 [0.01, 1]로
잘리므로 **`min(cap_mem_tokens) / 0.95 = 1,037,648`**이 정확히 나온다. 같은 항등식으로
인스턴스별 물리 토큰 수를 복원해 엔진의 점유율 합으로 나누면, **`vllm:kv_cache_usage_perc`가
분수로 쓰는 분모가 1,041,024 토큰(= 스케줄러 값 +0.3%)이지 1,152,240이 아니다.** 즉 두 수는
"엔진 대 스케줄러"가 아니라 **"기동 시 프로파일한 풀" 대 "런타임이 쓸 수 있는 풀"**이다.
→ **결론 방향에는 영향이 없다.** 어느 조합을 쓰든 8×8B의 `T*`는 실측 footprint 4,001보다
작다(B = 50 ms, ρ = 1.761 고정): `C` = 1,152,240에서 s = 0.783 / 0.707 / 0.630에 대해
**2,322 / 2,651 / 3,096**, `C` = 1,037,648에서 **2,035 / 2,314 / 2,688**이다. 여기서
s = 0.707은 `C` = 1,041,024로 환산한 점유율 기반 값이고 s = 0.630은 위 항등식으로 잰
스케줄러 자신의 공유비다. 아래 민감도 상자 [0.6, 1.0]이 셋을 다 덮는다.
→ 그림은 지시대로 엔진 보고 값 `C` = 1,152,240과 점유율 기반 s = 0.783을 쓰므로,
**여섯 조합 중 메모리 구속에 가장 불리한 쪽**을 그린다.
→ 70B와 Qwen run은 `cap_mem_tokens` 시리즈가 생기기 전이라 이 감사를 할 수 없다.

### 패널 (b) — 두 막대는 서로 다른 것을 센다

- **predicted**: 실측 footprint가 `T*`(chat 50 ms)보다 작은 **인스턴스 스크레이프**의 비율.
- **counted**: 스케줄러 자신의 `scheduler_fluidserve_infeasible_sole_total{reason}`에서
  `gate`(속도)가 차지하는 비율. 이것은 **후보 인스턴스 평가**를 세므로, 거절된 요청 하나가
  인스턴스 수만큼 기여하고 바쁜 순간에 몰린다.

| 함대 | predicted (반복 2) | counted (반복 2) | sole 평가 수 |
|---|---|---|---|
| 4×Llama-3.1-70B | **99.8%** (99.6~99.9) | **98.7%** (98.5~98.8) | 362,752 |
| 4×Qwen2.5-72B | **51.6%** (50.7~52.4) | **37.4%** (37.2~37.6) | 681,139 |
| 8×Llama-3.1-8B | **28.6%** (27.9~29.2) | **21.1%** (20.9~21.2) | 7,510,288 |

**둘이 같은 수가 아니라는 것이 아니라, 서로 아무 연결도 없는 두 곳에서 같은 순서와 같은
자릿수가 나온다는 것이 주장이다**(차이 1.1 / 14.2 / 7.5점).

⚠ **`sched_counter`의 라벨 파싱에 결함이 하나 있었고 고쳤다.** 8×8B의 두 반복은 서로 다른
스케줄러에서 돌았고 나중 것은 이 카운터에 `tier` 라벨을 하나 더 붙인다(`reason=gate,tier=25`).
키를 첫 `=`에서 잘라 뒤를 쓰면 한 run은 `gate`, 다른 run은 `gate,tier`가 되어 **한쪽의 속도
비율이 0.0%로 읽히고 아무도 그 이유를 말하지 않는다.** 지금은 라벨을 이름으로 파싱하고 나머지
라벨에 대해 합산한다. 고친 뒤 두 반복이 21.2%와 20.9%로 맞는다.

### 민감도 — 프롬프트에 적힌 문장을 정정한다

`ρ`를 1.0~3.0, `s`를 1.0~0.6으로 흔들었을 때(B = 50 ms):

| 함대 | 측정값에서의 `T*` | 상자 전체에서의 `T*` 구간 |
|---|---|---|
| 4×Llama-3.1-70B | 7,541 | **[3,130, 12,301]** |
| 4×Qwen2.5-72B | 5,141 | [2,902, 12,694] |
| 8×Llama-3.1-8B | 2,323 | **[1,383, 4,354]** |

⚠ **"두 함대의 `T*` 구간이 겹치지 않는다"는 것은 두 상수를 함대마다 독립으로 흔들면
사실이 아니다** — 70B의 하한 3,130과 8B의 상한 4,354가 겹친다. 맞는 진술은 둘이다.

1. **`ρ`와 `s`는 워크로드의 성질이므로 두 함대에서 같은 값을 쓴다.** 상자 안 어느 점에서든
   `T*_70B / T*_8B ∈ [2.26, 2.83]`이고 `T*_Qwen / T*_8B ∈ [2.10, 2.92]`다. **순위는 상자
   어디에서도 뒤집히지 않는다.**
2. **판정(자기 footprint가 자기 `T*`의 어느 쪽인가)도 견고하다.** 상자를 41×41로 훑으면
   8×8B는 **99%**에서 메모리 구속, 4×70B는 **88%**에서 속도 구속, 4×Qwen은 **72%**에서 속도
   구속으로 읽힌다. 8×8B가 속도 구속으로 뒤집히려면 측정된 s = 0.783에서 `ρ`가 **4.61**이어야
   하고(실측 1.76의 2.6배), s = 0.6까지 낮춰도 `ρ` > 2.58이 필요하다.

한 번에 하나씩만 흔들면(다른 하나는 측정값 고정) 구간은 겹치지 않는다: 8B [1,874, 3,054]
대 70B [5,197, 9,912](`ρ`만), 8B [1,714, 3,312] 대 70B [4,541, 9,359](`s`만).

### 캡션

> **Figure N: Which ceiling an instance reaches first.** (a) The crossover footprint T*,
> the per-request KV footprint at which an instance's pace ceiling and its memory ceiling
> meet, plotted against the per-token budget for the three profiled fleets. Dotted
> horizontal rules give each fleet's measured resident footprint over the load window;
> where a fleet's rule lies below its curve the pace ceiling binds, and where it lies
> above, memory binds. The shaded band varies rho over [1, 3] and s over [0.6, 1.0]
> jointly. The three fleets replay the same trace, so the ordering of their curves is a
> property of the model, the tensor parallelism and the KV pool alone. (b) Two
> independent accounts of how often the pace ceiling was the lower one: predicted from T*
> and the measured footprint, and counted from the scheduler's own sole-reason
> infeasibility counters. The two count different populations - instance scrapes against
> candidate evaluations at moments of refusal - and agree to within 14.2 points. The KV
> pool is measured for the two Llama fleets and derived from the per-GPU memory budget
> for the Qwen fleet; the decode step law is fitted on decode-only steps; rho, s and the
> resident footprint are measured per fleet over the load window.

## `effective_capacity.pdf` — EXP-139, in MOONCAKE's Figure 1 form

Single column, 3.335 x 2.45 in. Drawn by `fig_slo_scale_pace.py`; values in
`slo_scale_pace_outcome.csv`.

**Why this form, and where the metric name comes from.** MOONCAKE (FAST '25,
`related_works/`) draws its Figure 1 as a sweep of the TBT SLO on x against
"request capacity ratio" on y, with the improvement read as a percentage at fixed
SLO thresholds. It defines the quantity as "the proportion of effective requests
among all requests", an effective request being one that met both its TTFT and
its TBT threshold, and its scheduler rejects a request it cannot serve (algorithm
1, lines 18-19), so a rejection counts against the denominator. **That is the
same quantity this repository computes as attainment on every arrival.** The axis
carries their name because the term already exists; inventing one would be the
defect CLAUDE.md's 없는 용어를 만들지 않는다 rule is about.

**y** = effective request capacity (%): met / every arrival, rejected and
unfinished both counted as not met.
**x** = chat's per-token budget in ms. All three classes scale together --
the caption must give the other two (deepresearch 2x chat, swe 1.5x chat, and the
first-token budgets scale with them; EXP-139 section 3.1 has the table).

**Two differences from MOONCAKE's figure, both of which belong in the caption.**
Their x spans 10x (100 to 1000 ms) and is logarithmic; ours spans 1.86x and is
linear. **Their curves do not cross and ours do**, so the gap is annotated at two
budgets rather than one and its sign changes: +216% at 45 ms (FluidServe 80.3
against the best baseline, llm-d at 25.4) and -3% at 65 ms (81.6 against
PolyServe's 83.8). Annotating only the favourable one would be the omission the
exp-plot skill records under "report every scoring combination".

**The caption must also state** the three things the other EXP-139 figures state:
k=1.0 is a hollow marker from a different session (2026-08-31, movement up to 4.6
points); llm-d's repeat spread is 4.8 to 11.7 points against 0.0 to 0.7 for the
others; one 8-minute condition per point at 35 req/s, 4-point threshold fixed
before the run. The -3% at 65 ms is INSIDE that threshold and must not be read as
PolyServe winning.


## `slo_scale_plane.pdf` — EXP-139, what a policy spends against what it gets

Single column, 3.335 x 2.55 in. Drawn by `fig_slo_scale_pace.py`; values in
`slo_scale_pace_outcome.csv` (columns `budget_consumed`, `goodput_tok_s`,
`batch_p50`).

**Axes, and why these two.**
- x = **share of the per-token budget consumed** = delivered chat per-token time
  divided by chat's per-token budget at that scale. chat is the class that sets
  the gate on any instance holding it, so this is the budget that binds. x > 1 is
  a fleet running slower than the promise it was given, and the region is shaded.
  The dashed line at 0.90 is the pace FluidServe's gate plans against.
- y = **token goodput**: output tokens per second from requests that met their
  SLO. Request goodput is NOT a second axis: at a fixed arrival rate it is the
  attainment rescaled, measured within 0.06 req/s on every cell, so it would be
  the same picture twice.
- marker area = **fleet decode batch**, linear from 150 to 1070 onto 8 to 62
  pt^2. Concurrency is not independent -- Little's law makes it the admitted rate
  times the residence time -- so it is a third reading, not a third claim.

**What the figure says.** Three of the four policies TRAVERSE this plane as the
budget is scaled and one does not. FluidServe's four budgets land on one point
(x 0.87 to 0.90, y 11.9 to 13.8k) with the largest markers. PolyServe sweeps from
x=1.41 to x=0.86, crossing the budget line, and its goodput only rises once it is
inside. Llumnix SLO never leaves the over-budget region. llm-d stays under budget
(x 0.75 to 0.85) and converts less of it.

**A composite scalar was considered and rejected.** Any single "effective serving
capacity" number here would be close to a monotone function of goodput, because
request goodput is attainment rescaled and batch depth follows from admission and
residence time. It would also be a term with no definition in the literature. The
plane shows the trade instead of hiding it in one number.

**The caption must state** the same three things as the two figures below (k=1.0
is a different session and is not drawn here at all; llm-d's repeat spread is
4.8 to 11.7 points; one 8-minute condition per point, 4-point threshold).

**What may NOT be said:** that a point further right is "using the hardware
better". x > 1 means the fleet is slower than its promise, which is a failure,
not a higher utilisation.


## `slo_scale_pace.pdf` / `slo_scale_outcome.pdf` — EXP-139, the SLO scale sweep

Two single-column figures (3.335 x 2.30 in, include at that width, no scaling).
Both are drawn by `slo_scale.py`; the values they draw are in `slo_scale.csv`,
written by the same run of the same script.

**Runs.** Static, 2100 rpm (35 req/s), 8 minutes per condition, Llama-3.1-70B x4
TP=2, scheduler `063e1c85`.
`results/*exp139k{070,080,090,110,120,130}r[12]_*` for the six scales measured on
2026-09-15, and `results/*exp108r[12]_*_rpm_2100` for the deployed budget k=1.0,
measured 2026-08-31. PRERUN directories (llm-d's discarded 3-minute warm-up) are
excluded everywhere.

**n per cell.** 2 repeats at k=0.7 and k=1.3 for FluidServe / llm-d / PolyServe
(Llumnix SLO has 2 at 0.7, 1 at 1.3), 2 at k=1.0 for all four, and 1 elsewhere.
k=0.8 and k=1.2 are PolyServe only -- they were run as a probe after the model in
EXP-139 section 8.6 predicted them. The `n_repeats` column carries this per row
and the error bars are min..max, so a point with no bar is a single run.

**What the budgets are.** Every budget is scaled by k, first-token and per-token
together: chat (5000 ms, 50 ms/token) at k=1. FluidServe receives the per-token
budgets through `--fluidserve-class-budgets` with the tier keys held at
25/50/100; the other three read `slo.<class>.tbt_ms` from their workload file.
EXP-139 section 3.1 lists all six values per scale.

**Metric definitions.**
- *attainment*: every arrival is the denominator; a rejected or unfinished
  request is a violation. From `all_arrivals_attainment.one_run`, the
  repository's own scorer, imported rather than reimplemented.
- *delivered per-token time*: `(e2e - first token) / (output tokens - 1)`, median
  over chat requests that were admitted and ran to completion. chat is the class
  that sets the gate on any instance holding it, so it is the quantity the budget
  is meant to control. It is computed from the client's columns and does not
  depend on the scoring rule.
- *0.90 x budget*: the pace FluidServe's gate plans against
  (`fsAllowanceUtilisation = 0.90` in `fluidserve.go:92`, `gateSlack` 1.000).

**The caption must state:**
- k=1.0 is drawn as a HOLLOW marker and is not joined into the curves: it is the
  only point from a different session (2026-08-31), and session-to-session
  movement on this workload has been measured at up to 4.6 points. PolyServe's
  pace at that point reads 64.3 ms against 55.9-56.7 at every 2026-09-15 scale,
  which is the same session effect seen in the pace rather than a response to the
  budget.
- llm-d's repeat spread is 4.8 points at k=1.3 and 11.7 at k=0.7, against 0.0-0.7
  for the other three arms. Its predictor is reset per condition and trained only
  by a 3-minute pre-run (EXP-139 section 7.1), so differences involving that arm
  are not read at the 4-point threshold the other arms use.
- Every point is one 8-minute condition at one arrival rate; the pre-registered
  threshold for reading a difference is 4 points (EXP-139 section 5).

**What may NOT be said from these figures:**
- That PolyServe beats FluidServe at k=1.3. The gap is +1.9 to +2.6 on every
  arrival and reproduces (PolyServe 83.8/83.8, FluidServe 81.9/81.2, ranges not
  overlapping), but it is below the threshold fixed before the run, and on token
  goodput it is +0.3%.
- That PolyServe "rejects" the requests it does not serve. At k=1.3 its policy
  refuses nothing; all 2,186 client-side rejections are the gateway giving up at
  its 5-second ceiling (EXP-139 section 8.7).

## `budget_cdf.pdf` — EXP-139, both performance axes in one curve

Two panels, 4.05 x 2.25 in (include at that width). Drawn by `fig_budget_cdf.py`;
`budget_cdf.csv` carries each curve at 5-point quantile steps.

**The problem it solves.** A policy has to be judged on two things that are
normally two figures: how much it delivers, and how close it runs to the latency
budget it was given. Encoding the second as marker area does not read.

**The device.** Per request, take the WORSE of its two terms against its own
budget -- `max(first-token / first-token budget, per-token / per-token budget)`
-- and plot the CDF over EVERY arrival. All three classes then share one axis,
and since a request meets its SLO exactly when both terms are under 1, the SLO is
the single line x = 1. One curve carries three readings:

- **height at x = 1** = SLO attainment on every arrival. Verified against the
  scored table on all eight (panel, arm) cells: agreement within 0.3 points, the
  residual being one-output-token requests, for which a per-token time is
  undefined.
- **where the curve stands up** = how much of the budget the policy spends.
  FluidServe's step is at 0.87-0.90 in both panels, which is its gate's target
  (`fsAllowanceUtilisation = 0.90`); llm-d rises from 0.4 and leaves headroom;
  PolyServe stands up at 1.1-1.35 when tight and just under 1.0 when loose, which
  is the same fixed 56 ms pace meeting two different budgets.
- **the plateau** = the share that produced a per-token time at all, so 100 minus
  it is rejection plus the requests cut off at the end of the window.

**Why the axis is the max and not the per-token term.** Drawn on the per-token
term alone, the Llumnix SLO curve reaches 24% at x = 1 in the loose panel while
its measured attainment is 6.7 -- that policy fails on FIRST TOKEN, not on pace
(deepresearch 62.6% and swe 77.0% over their first-token budgets at k = 1.3). The
figure would have contradicted the table.

**Caption must state:** one repeat per curve (the panels show single runs, not
pooled repeats, so that two runs are never hidden inside one curve); 35 req/s,
8-minute conditions; the two panels are k = 0.9 and k = 1.3 of EXP-139 and the
budgets at each are in section 3.1 of that file.
