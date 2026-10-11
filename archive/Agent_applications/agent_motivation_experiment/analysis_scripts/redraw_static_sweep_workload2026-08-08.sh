#!/usr/bin/env bash
# The standard rate-sweep figure set for the FIVE control planes on the workload
# as it has stood since the 2026-08-08 load-generator fix.
#
# It was four until 2026-08-10, when EXP-77 added the vLLM router's default
# cache_aware policy. The four existing curves are unchanged by that; nothing in
# their globs moved.
#
# The name carries the date rather than saying "post-fix", because this project
# has two boundaries -- the 2026-08-08 workload fix and the 2026-08-09 hour-trace
# band change -- and "post-fix" does not say which. It also does not carry an
# experiment number, which is the convention for everything else under
# results/aggregate_analysis/, because the four arms come from four experiments:
# EXP-68/69/70 for FluidServe v0.2 and llm-d, EXP-72 for the other two. Calling
# it exp72 would put that number on two arms it did not measure.
#
# Why a fourth redraw script. redraw_static_sweep.sh draws the canonical
# comparison from EXP-53/57 and redraw_static_sweep_llmd.sh adds llm-d to it;
# both are on the workload from BEFORE the load-generator fix, where twelve
# workers sent identical prompt streams and the engines' prefix cache hit rate
# was inflated from 28.9% to 83-86%. Those figures are cited as they are. This
# one draws only post-fix runs, and writes to its own directory so neither of
# the others changes under a document that already quotes it.
#
# The arm globs are written out one arm at a time rather than widened, because
# the pre-fix sweeps are still on disk (84 conditions under exp53/54/57) and a
# looser pattern averages them in without saying so. That has happened here
# before: EXP-53's PolyServe was superseded by EXP-57 and anything still
# globbing exp53 averaged 27.5 and 35.6 into 31.6 and drew a policy that never
# ran. The condition count behind every glob is printed before anything is drawn.
#
#   FluidServe v0.2  fspfx     EXP-68s/68r/69 (35-70, two repeats) + EXP-70 (10-25, one)
#   llm-d            llmdslo   EXP-68s/68r    (35-70, two repeats) + EXP-70 (10-25, one)
#   Llumnix SLO      slo       EXP-72 (all eight rates, ONE repeat)
#   PolyServe        polyserve EXP-72 (all eight rates, ONE repeat)
#   vLLM router      vllmcache EXP-77 (all eight rates, TWO repeats -- the only arm
#                               here with a repeat at every rate)
#
# EXP-73's fspfx conditions at 25, 35 and 45 are deliberately NOT in the
# FluidServe glob. They are the control arm of an ablation ladder and are the
# same configuration, so they are legitimate repeats -- but including them would
# give one arm four repeats at three rates while every other arm has one or two,
# and the figure would then be drawn from a different number of runs per point
# per arm with nothing on the image saying so.
#
# Two things that have to be said next to these figures rather than discovered:
#
#   Every rate below 35 req/s is one repeat for all four arms, and EXP-72 is one
#   repeat throughout. All four knees fall in single-repeat territory, so a point
#   with no error bar is not a precise point -- it is an unrepeated one.
#
#   Llumnix SLO and llm-d take the m1f workload config, the other two take m1.
#   Neither of those policies can express an end-to-end budget, so left on m1
#   they read the agent class's 25 ms tier key as a per-token target and reject
#   almost all of it. Scoring is unaffected: that class is judged end-to-end at
#   30 s whatever the config says.
#
#   bash analysis_scripts/redraw_static_sweep_workload2026-08-08.sh [out-dir]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/static_sweep_workload2026-08-08}
R=analysis_scripts/request_level
mkdir -p "$OUT"

FS='results/*exp68s*_fspfx_m1_rpm_* results/*exp68r*_fspfx_m1_rpm_* results/*exp69*_fspfx_m1_rpm_* results/*exp70*_fspfx_m1_rpm_*'
LD='results/*exp68s*_llmdslo_m1f_rpm_* results/*exp68r*_llmdslo_m1f_rpm_* results/*exp70*_llmdslo_m1f_rpm_*'
SL='results/*exp72r1_slo_m1f_rpm_*'
PS='results/*exp72r1_polyserve_m1_rpm_*'
VC='results/*exp77r*_vllmcache_m1_rpm_*'

echo "=== 조건 수 (glob마다)"
tot=0
for pair in "FluidServe v0.2|$FS" "llm-d|$LD" "Llumnix SLO|$SL" "PolyServe|$PS" "vLLM router|$VC"; do
  name=${pair%%|*}; g=${pair#*|}
  n=$(ls -d $g 2>/dev/null | grep -vc PRERUN)
  printf "  %-16s %2d\n" "$name" "$n"
  tot=$((tot+n))
done
echo "  합계 $tot"
# The pre-fix sweeps must not be inside any of the globs above. Counting them
# separately is the check: if this number changes the totals, a glob widened.
echo "  (같은 디스크에 수정 전 조건 $(ls -d results/*exp5[347]*_rpm_* 2>/dev/null | wc -l)개가 있고 위 glob에 걸리지 않아야 한다)"
[ "$tot" -ge 48 ] || { echo "ABORT: 조건이 48개 미만이다 (다섯 arm × 8 rate 이상이어야 한다)"; exit 1; }

# The title and the note are in English because the serif font these figures are
# drawn in carries no Hangul, so Korean text renders as empty boxes. matplotlib
# says so as a UserWarning and still writes the file.
# Kept to two lines. A note long enough to wrap six times pushes the axes into
# the lower two thirds of the canvas, and the detail belongs in the experiment
# file rather than on the image. THIS HAPPENED AGAIN on 2026-08-10, when the
# fifth arm was added and the note grew to eleven lines: it covered the legend
# and pushed the axes below the midline. Anything longer than the two lines
# below goes in README.md in the output directory, which is where a reader who
# needs the provenance will look anyway.
NOTE="A point with no error bar is ONE run, not a precise one. The vLLM router has two repeats at every rate (EXP-77); the other four have two only at 35-70 req/s, so four of the five 90% crossings sit on unrepeated points. Llumnix SLO and llm-d take the m1f config; the agent class is scored end-to-end at 30 s either way. Provenance per arm and per rate: README.md beside this figure."

echo
echo "=== 달성률·goodput·처리량 (요청 단위, 두 분모)"
python3 "$R/exp27_figures.py" --runs $FS $LD $SL $PS $VC --out-dir "$OUT" \
  --title "Five control planes, static arrival-rate sweep (post-fix workload)" --note "$NOTE" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== 정책 나란히, 도착률마다 (엔진 계층)"
for rpm in 600 1200 1500 2100 2700 4200; do
  python3 "$R/exp38_policy_compare.py" \
    --runs "results/*exp72r1_polyserve_m1_rpm_$rpm" \
           "results/*exp72r1_slo_m1f_rpm_$rpm" \
           "results/*exp6[89]*_llmdslo_m1f_rpm_$rpm" \
           "results/*exp70*_llmdslo_m1f_rpm_$rpm" \
           "results/*exp6[89]*_fspfx_m1_rpm_$rpm" \
           "results/*exp70*_fspfx_m1_rpm_$rpm" \
           "results/*exp77r*_vllmcache_m1_rpm_$rpm" \
    --out-dir "$OUT" --title "static $((rpm/60)) req/s" >/dev/null 2>&1 \
    && echo "  $((rpm/60)) req/s ok" || echo "  $((rpm/60)) req/s FAILED"
done

echo
echo "=== 조건 하나짜리 여섯 패널 (arm마다)"
for pair in "fspfx|$FS" "llmdslo|$LD" "slo|$SL" "polyserve|$PS" "vllmcache|$VC"; do
  arm=${pair%%|*}; g=${pair#*|}
  for d in $g; do
    [ -d "$d" ] || continue
    python3 "$R/plot_ratesweep_split.py" --glob "$d" --out-dir "$OUT/percond" >/dev/null 2>&1
  done
  echo "  $arm 완료"
done

echo
echo "=== 엔진 계층, 한 줄에 (run, 엔진) 하나"
python3 "$R/engine_occupancy.py" --summary-only \
  --runs "results/*exp72r1_*_rpm_*" --csv "$OUT/engine_layer_exp72.csv" 2>&1 | tail -2

echo
echo "=== 그려진 것 — arm 이름과 점 개수를 확인한다"
ls -1 "$OUT" | sed 's/^/  /'
