#!/usr/bin/env bash
# EXP-97's hour trace: the class preference's normalisation, three arms on one set
# of axes. A snapshot of redraw_hour_trace_exp71_four.sh rather than an argument to
# it, for the reason that script gives about its own predecessors -- documents cite
# those output directories and a shared script would change their figures.
#
# Arms, in plain terms:
#   선호 켬 (지금 방식)   fspfx   ranks by the class's share of THAT instance's
#                                 occupancy, which saturates at 1.0            EXP-97
#   선호 켬 (고친 방식)   fscount ranks by how many of the class it holds        EXP-97
#   선호 끔               fsnoaff no class term at all                          EXP-93
#
# The preference-off arm comes from EXP-93 because this binary does not touch its
# code path and EXP-93 measured it on this same trace with two repeats, spread 0.3.
# It carries no counters, which is fine: with no class term every candidate has
# share 0 and the top-share counter is degenerate.
#
#   bash analysis_scripts/redraw_hour_trace_exp97.sh [out-dir] [pass]
#     pass: "" for the first (exp97r1), "b" for the second (exp97br1)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp97_metric}
PASS=${2:-}
R=analysis_scripts/request_level
mkdir -p "$OUT"

SH=$(ls -d results/*exp97${PASS}r1_fspfx_shift 2>/dev/null | head -1)
CO=$(ls -d results/*exp97${PASS}r1_fscount_shift 2>/dev/null | head -1)
NO=$(ls -d results/*exp93${PASS:+n}${PASS}r1_fsnoaff_shift 2>/dev/null | head -1)
[ -n "$NO" ] || NO=$(ls -d results/*exp93n*_fsnoaff_shift 2>/dev/null | head -1)
# llm-d comes from EXP-93 and is NOT re-measured for this binary. The commit that
# introduced the affinity metric touches pkg/scheduler/policy/fluidserve.go, its
# tests, and one flag definition in cmd/config/config.go; nothing on llm-d's path.
# `sortCandidates` appears outside fluidserve.go in exactly one place, inside that
# flag's help text, which is a string and not a call. The llm-d driver restarts
# three pods in the llmd namespace and never touches deploy/scheduler or
# deploy/gateway, because that arm routes through its own external processor.
LD=$(ls -d results/*exp93${PASS}r1_llmdslo_shift 2>/dev/null | head -1)

echo "=== run selection (pass '${PASS:-1}')"
MISSING=0
for pair in "preference on, shipped|$SH" "preference on, fixed|$CO" "preference off|$NO" "llm-d|$LD"; do
  name=${pair%%|*}; dir=${pair#*|}
  if [ -n "$dir" ]; then printf "  %-22s %s\n" "$name" "$dir"
  else printf "  %-22s MISSING\n" "$name"; MISSING=1; fi
done
[ "$MISSING" = 1 ] && echo "  -> drawing with the arms that exist. This is NOT the full set."

echo
echo "=== engine attribution"
for d in "$SH" "$CO" "$NO"; do
  [ -n "$d" ] || continue
  [ -f "$d/analysis/request_engine.csv" ] && { echo "  $(basename "$d") already built"; continue; }
  python3 "$R/build_request_engine_map.py" "$d" 2>&1 | tail -1 | sed 's/^/  /'
done

SERIES=(); HOUR=(); RUNS=()
[ -n "$NO" ] && { SERIES+=("preference off|#9467bd|-|$NO");        HOUR+=("preference off|#9467bd|$NO");        RUNS+=("$NO"); }
[ -n "$SH" ] && { SERIES+=("preference, shipped|#17becf|-|$SH");   HOUR+=("preference, shipped|#17becf|$SH");   RUNS+=("$SH"); }
[ -n "$CO" ] && { SERIES+=("preference, fixed|#d62728|-|$CO");     HOUR+=("preference, fixed|#d62728|$CO");     RUNS+=("$CO"); }
[ -n "$LD" ] && { SERIES+=("llm-d|#ff7f0e|--|$LD");            HOUR+=("llm-d|#ff7f0e|$LD");            RUNS+=("$LD"); }

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv \
  --knee 28.0 "FluidServe v0.2 on m1" \
  --mean-input 533 3814 6215 \
  --title "EXP-97 hour trace: the same Azure-shaped arrival band as EXP-93, 10.8-45.0 req/s, with the class mix stepping m2 A m1 B every 15 minutes so chat runs 93.0, 33.3, 76.9 and 60.0 percent of the requests. The knee is m1's measured 28.0 req/s; the other three segments have their own and none is measured." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline"
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT" \
  --title "EXP-97, class-preference normalisation" --series "${SERIES[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# Naming the runs instead of globbing for them: the four arms come from three
# sessions, so no single pattern addresses the set, and `*r1_fspfx_shift*`
# matches exp97r1 and exp97br1 both -- drawing pass 1 with pass 2's engine data.
EV=()
[ -n "$NO" ] && EV+=("fsnoaff=$NO")
[ -n "$SH" ] && EV+=("fspfx=$SH")
[ -n "$CO" ] && EV+=("fscount=$CO")
[ -n "$LD" ] && EV+=("llmdslo=$LD")
python3 "$R/exp41_engine_view.py" --variant shift --out-dir "$OUT" \
  --run "${EV[@]}" --title "EXP-97" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the four arms side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "EXP-97 hour trace, class-preference normalisation" \
  >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== goodput per class over the hour"
python3 "$R/exp53_class_goodput.py" --out "$OUT" --hour "${HOUR[@]}" \
  --hour-name class_goodput_hour.png \
  >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== per-run engine, token and control-plane panels"
for d in "${RUNS[@]}"; do
  python3 "$R/plot_ratesweep_split.py" --glob "$d" --out-dir "$OUT" \
    >/dev/null 2>&1 && echo "  $(basename "$d") ok" || echo "  $(basename "$d") FAILED"
done

echo
echo "=== separation: how many instances each class runs on"
python3 "$R/separation_measures.py" "${RUNS[@]}" \
  --csv "$OUT/separation_hour.csv" 2>&1 | tail -24
