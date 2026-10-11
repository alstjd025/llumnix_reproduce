#!/usr/bin/env bash
# EXP-109 hour trace: five policies on the mix-shift hour, with the agent class
# promised per token (TTFT 7 s + 75 ms/token) on every arm that reads the
# promise. A snapshot of redraw_hour_trace_exp107.sh rather than an argument to
# it, because the arm set and the swe rule both differ.
#
# The set grows as the chain finishes: every arm is selected per repeat, an
# unmerged run is reported and skipped, and a missing repeat is reported and
# skipped. The same command therefore draws the interim set now and the full set
# later.
#
#   bash analysis_scripts/redraw_hour_trace_exp109.sh [out-dir]
#
# ⚠ EVERY series is scored `tok:7:75` -- TTFT <= 7 s AND mean per-token <= 75 ms
# for the agent class. That is the promise all five arms ran under, so unlike
# EXP-107 there is no mixing of forms here. The swe columns are NOT comparable
# with any e2e-scored figure.
#
# ⚠ The FluidServe series includes a THIRD repeat from another session:
# 260827_2320_exp107tr1_fsv3capgnofrct75_shift, taken 2026-08-28 on the same
# binary 6dc9f035 that is deployed now, on the same trace and the same _t75
# workload file. It is drawn as its own line rather than averaged in, so the
# between-session movement is visible instead of hidden.
#
# ⚠ The run-boundary artifact is NOT removed by this script. A request still in
# flight when the hour ends leaves both denominators, so the last windows of a
# BACKLOGGED arm keep only the requests that finished -- the fast ones -- and
# read far too high. Run in_flight_at_end.py (printed at the end) before reading
# the last minutes of any attainment timeline here, and cut every arm at the
# same minute.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp109_hour_t75}
R=analysis_scripts/request_level
mkdir -p "$OUT"

# rep 0 = the EXP-107T run, same arm and binary, another session.
# `ls | head -1` picks the OLDEST directory a pattern matches, and a cell that
# was measured more than once leaves every attempt on disk. exp109r1's vLLM
# router matches five: the 293-minute run that never terminated, three
# header-only leftovers from aborted attempts, and the good one. head -1 would
# have drawn the 293-minute run. pick_usable_run.sh takes the most RECENT
# directory whose metrics.csv is merged and whose end-to-end span is at most 90
# minutes, so an attempt that did not terminate can never become the figure.
PICK=/home/nxclab/tools/pick_usable_run.sh
F0=$($PICK 'results/*exp107tr1_fsv3capgnofrct75_shift')
F1=$($PICK 'results/*exp109r1_fsv3capgnofrct75_shift')
F2=$($PICK 'results/*exp109r2_fsv3capgnofrct75_shift')
L1=$($PICK 'results/*exp109r1_llmdslot75_shift')
L2=$($PICK 'results/*exp109r2_llmdslot75_shift')
P1=$($PICK 'results/*exp109r1_polyservept75_shift')
P2=$($PICK 'results/*exp109r2_polyservept75_shift')
S1=$($PICK 'results/*exp109r1_slot75_shift')
S2=$($PICK 'results/*exp109r2_slot75_shift')
V1=$($PICK 'results/*exp109r1_vllmcachet75_shift')
V2=$($PICK 'results/*exp109r2_vllmcachet75_shift')

# A directory exists from the moment its condition STARTS; metrics.csv is merged
# only at the end. A header-only file (~600 bytes) marks exactly that state, and
# a leftover shards/ directory marks a merge that did not finish.
complete() {
  [ -n "$1" ] && [ -s "$1/metrics.csv" ] \
    && [ "$(wc -c < "$1/metrics.csv")" -gt 100000 ] && [ ! -d "$1/shards" ]
}
for v in F0 F1 F2 L1 L2 P1 P2 S1 S2 V1 V2; do
  d=${!v}
  if [ -n "$d" ] && ! complete "$d"; then
    echo "  NOTE: $(basename "$d") is incomplete (metrics.csv not merged) -- skipped"
    eval "$v="
  fi
done

echo "=== run selection"
MISSING=0
for pair in "FluidServe EXP-107T|$F0" "FluidServe rep 1|$F1" "FluidServe rep 2|$F2" \
            "llm-d rep 1|$L1" "llm-d rep 2|$L2" \
            "PolyServe rep 1|$P1" "PolyServe rep 2|$P2" \
            "Llumnix SLO rep 1|$S1" "Llumnix SLO rep 2|$S2" \
            "vLLM router rep 1|$V1" "vLLM router rep 2|$V2"; do
  name=${pair%%|*}; dir=${pair#*|}
  if [ -n "$dir" ]; then printf "  %-22s %s\n" "$name" "$(basename "$dir")"
  else printf "  %-22s MISSING\n" "$name"; MISSING=1; fi
done
[ "$MISSING" = 1 ] && echo "  -> drawing with the arms that exist. This is NOT the full set."

echo
echo "=== engine attribution"
for d in "$F0" "$F1" "$F2" "$L1" "$L2" "$P1" "$P2" "$S1" "$S2" "$V1" "$V2"; do
  [ -n "$d" ] || continue
  [ -f "$d/analysis/request_engine.csv" ] && { echo "  $(basename "$d") already built"; continue; }
  # llm-d routes through Envoy and the EPP, not the Llumnix scheduler, so its
  # dispatch log has no entries and build_request_engine_map attributes 0% of
  # its requests -- silently, as an empty per-engine panel. Its attribution
  # comes from the Envoy access log instead.
  case "$d" in
    *llmdslo*) python3 "$R/llmd_engine_map.py" "$d" 2>&1 | tail -1 | sed 's/^/  /' ;;
    *)         python3 "$R/build_request_engine_map.py" "$d" 2>&1 | tail -1 | sed 's/^/  /' ;;
  esac
done

# Colours are the fixed arm colours so they mean the same thing across figures:
# FluidServe #1f77b4, llm-d #8c564b, PolyServe #d62728, Llumnix SLO #2ca02c.
# The vLLM router has no registry colour; #7f7f7f (grey) is used and is not
# reused by any other arm here. Repeats take the lighter shade of the same hue.
SERIES=(); HOUR=(); RUNS=(); EV=()
# The engine-layer figure takes ONE run per arm, keyed by the arm name, because
# its key must be a name registered in that script's ARMS table -- an
# unregistered key aborts it, which is the behaviour that stopped this figure
# from being silently drawn with half its arms. Repeats go to the timeline and
# the side-by-side, which take a list.
add() {  # $1 label  $2 colour  $3 linestyle  $4 dir  $5 arm-name-or-empty
  [ -n "$4" ] || return 0
  SERIES+=("$1|$2|$3|$4|tok:7:75"); HOUR+=("$1|$2|$4"); RUNS+=("$4")
  [ -n "${5:-}" ] && EV+=("$5=$4")
  return 0
}
add "FluidServe (EXP-107T)" "#aec7e8" "-"  "$F0" ""
add "FluidServe rep 1"      "#1f77b4" "-"  "$F1" fsv3capgnofrct75
add "FluidServe rep 2"      "#17becf" "-"  "$F2" ""
add "llm-d rep 1"           "#8c564b" "--" "$L1" llmdslot75
add "llm-d rep 2"           "#c49c94" "--" "$L2" ""
add "PolyServe rep 1"       "#d62728" "-." "$P1" polyservept75
add "PolyServe rep 2"       "#ff9896" "-." "$P2" ""
add "Llumnix SLO rep 1"     "#2ca02c" ":"  "$S1" slot75
add "Llumnix SLO rep 2"     "#98df8a" ":"  "$S2" ""
add "vLLM router rep 1"     "#7f7f7f" "-"  "$V1" vllmcachet75
add "vLLM router rep 2"     "#c7c7c7" "-"  "$V2" ""

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv \
  --knee 28.0 "FluidServe v0.2 on m1" \
  --mean-input 533 3814 6215 \
  --title "EXP-109 hour trace: Azure-shaped arrival band 10.8-45.0 req/s, class mix stepping m2 A m1 B every 15 minutes (chat 93.0, 33.3, 76.9, 60.0 percent of requests). The knee is m1's measured 28.0 req/s for v0.2; the other segments have their own and none is measured." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline, TWO versions, because the arms stop being readable at"
echo "    different minutes and that difference is itself the result."
echo "    in_flight_at_end.py: the four arms that reject cross 20% unfinished at"
echo "    minute 60 (the run boundary); the vLLM router, which rejects nothing,"
echo "    crosses at minute 25 and is at 100% from minute 58. Cutting all five at"
echo "    25 would throw away 35 minutes of the other four, so:"
echo "      (a) the four that terminate, cut at 60"
echo "      (b) all five, cut at 25 -- the only window where five can be compared"
SER4=(); for x in "${SERIES[@]}"; do case "$x" in *vllmcache*) ;; *) SER4+=("$x");; esac; done
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT" --cut-min 60 \
  --title "EXP-109, the four policies that reject, agent class promised TTFT 7 s + 75 ms/token (cut at minute 60)" \
  --series "${SER4[@]}" \
  && echo "  (a) ok" || echo "  (a) FAILED"
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT/cut25" --cut-min 25 \
  --title "EXP-109, all five, cut at minute 25 -- where the vLLM router still had a readable outcome" \
  --series "${SERIES[@]}" \
  && echo "  (b) ok" || echo "  (b) FAILED"

echo
echo "=== engine layer over the hour"
python3 "$R/exp41_engine_view.py" --variant shift --out-dir "$OUT" \
  --run "${EV[@]}" --title "EXP-109" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the runs side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "EXP-109 hour trace, five policies, agent class per token" \
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
echo "=== separation, per class: how many instances each class runs on"
python3 "$R/separation_measures.py" "${RUNS[@]}" \
  --csv "$OUT/separation_hour.csv" 2>&1 | tail -25

echo
echo "=== ⚠ before reading the last minutes of any timeline above:"
echo "    python3 $R/in_flight_at_end.py ${RUNS[*]}"
echo "    -- drop the windows whose in-flight-at-end share exceeds 20% and cut"
echo "       EVERY arm at the same minute. A backlogged arm keeps only the fast"
echo "       requests at the end and reads far too high."
