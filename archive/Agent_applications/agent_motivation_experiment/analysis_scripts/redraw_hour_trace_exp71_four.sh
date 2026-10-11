#!/usr/bin/env bash
# EXP-71's hour trace with all FOUR control planes on one set of axes.
#
# Why a third script rather than an argument to the other two. redraw_hour_trace.sh
# draws the canonical hour comparison on the 25-75 req/s band and the pre-2026-08-08
# workload, and motivation.md cites its output directory. redraw_hour_trace_exp71.sh
# draws the two arms EXP-71 started with, FluidServe v0.2 and llm-d, and the numbers
# in EXP-71 sections 3.1 to 3.4 come from it. This one adds Llumnix SLO and PolyServe,
# which arrived later, and it writes to its own directory so neither of those two sets
# of figures changes underneath a document that already cites it.
#
# Every arm here is on the SAME workload and the SAME 10.8-45.0 req/s band, so the
# four are comparable point for point. That was not true of any earlier hour figure:
# EXP-54's four arms predate the load-generator fix, and EXP-71's first half had only
# two arms.
#
# Arm names, because the directory names and the paper names disagree on purpose:
#   FluidServe v0.2  arm `fspfx`,     variant `fullb`   (prefix awareness on = the default)
#   llm-d            arm `llmdslo`,   variant `full`    (a different driver, hence `full`)
#   Llumnix SLO      arm `slo`,       variant `fullb`   (takes the m1f workload config)
#   PolyServe        arm `polyserve`, variant `fullb`
#
# Colours are the repository's fixed policy colours so they mean the same thing in
# every figure: FluidServe cyan, PolyServe red, Llumnix SLO green, llm-d brown.
#
#   bash analysis_scripts/redraw_hour_trace_exp71_four.sh [out-dir] [pass]
#     pass: "" for the first (exp71r1), "b" for the second (exp71br1)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp71_four}
PASS=${2:-}
R=analysis_scripts/request_level
mkdir -p "$OUT"

FS=$(ls -d results/*exp71${PASS}r1_fspfx_fullb 2>/dev/null | head -1)
LD=$(ls -d results/*exp71${PASS}r1_llmdslo_full 2>/dev/null | head -1)
SL=$(ls -d results/*exp71${PASS}r1_slo_fullb 2>/dev/null | head -1)
PS=$(ls -d results/*exp71${PASS}r1_polyserve_fullb 2>/dev/null | head -1)

echo "=== run selection (pass '${PASS:-1}')"
MISSING=0
for pair in "FluidServe v0.2|$FS" "llm-d|$LD" "Llumnix SLO|$SL" "PolyServe|$PS"; do
  name=${pair%%|*}; dir=${pair#*|}
  if [ -n "$dir" ]; then printf "  %-16s %s\n" "$name" "$dir"
  else printf "  %-16s MISSING\n" "$name"; MISSING=1; fi
done
# Draw what is there rather than aborting: the second pass lands one arm at a time,
# and a three-arm figure now is worth more than no figure until the fourth finishes.
# It is named on stdout so nobody mistakes a partial figure for the full set.
[ "$MISSING" = 1 ] && echo "  -> drawing with the arms that exist. This is NOT the full set."

echo
echo "=== llm-d engine attribution from the Envoy access log"
# llm-d does not go through the Llumnix scheduler, so the engine each request landed
# on comes from Envoy's upstream-host field and has to be built before any engine
# figure can see it. Doing it here rather than assuming it was done.
[ -n "$LD" ] && python3 "$R/llmd_engine_map.py" "$LD" 2>&1 | tail -2

echo
echo "=== engine attribution for the three scheduler-based arms"
for d in "$FS" "$SL" "$PS"; do
  [ -n "$d" ] || continue
  [ -f "$d/analysis/request_engine.csv" ] && { echo "  $(basename "$d") already built"; continue; }
  python3 "$R/build_request_engine_map.py" "$d" 2>&1 | tail -1 | sed 's/^/  /'
done

SERIES=(); HOUR=(); RUNS=()
[ -n "$PS" ] && { SERIES+=("PolyServe|#d62728|-|$PS");      HOUR+=("PolyServe|#d62728|$PS");      RUNS+=("$PS"); }
[ -n "$SL" ] && { SERIES+=("Llumnix SLO|#2ca02c|-|$SL");    HOUR+=("Llumnix SLO|#2ca02c|$SL");    RUNS+=("$SL"); }
[ -n "$LD" ] && { SERIES+=("llm-d|#8c564b|-|$LD");          HOUR+=("llm-d|#8c564b|$LD");          RUNS+=("$LD"); }
[ -n "$FS" ] && { SERIES+=("FluidServe v0.2|#17becf|-|$FS"); HOUR+=("FluidServe v0.2|#17becf|$FS"); RUNS+=("$FS"); }

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_short_m123_b1045.csv \
  --knee 28.1 "FluidServe v0.2" --knee 18.7 "llm-d" \
  --mean-input 687 4538 6758 \
  --title "EXP-71 hour trace: an Azure-shaped arrival rate rebanded to 10.8-45.0 req/s, with the class mix stepping m1 m2 m3 m1 every 15 minutes. Mean input tokens are the measured 687 / 4,538 / 6,758. The two knees are EXP-70's, measured on the same workload." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline, four policies"
python3 "$R/exp41_dynamic_timeline.py" --variant fullb --out-dir "$OUT" \
  --title "EXP-71, four control planes" --series "${SERIES[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# Trailing wildcard: llm-d's variant is `full`, the other three are `fullb`.
python3 "$R/exp41_engine_view.py" --variant full --out-dir "$OUT" \
  --pattern "results/*exp71${PASS}r1_{arm}_{variant}*" --title "EXP-71" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the four policies side by side"
# --tag forces all four into one cell. Without it the script groups by the tag it
# parses out of the directory name, and llm-d's variant is `full` while the other
# three are `fullb`, so it silently drew two figures with one arm and three arms.
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "EXP-71 hour trace, four control planes" \
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
echo "=== separation: how many instances each class runs on, and the score beside it"
python3 "$R/separation_measures.py" "${RUNS[@]}" \
  --csv "$OUT/separation_hour.csv" 2>&1 | tail -24

echo
echo "=== engine layer, one row per (run, engine)"
# The window keeps the PolyServe run's idle tail out of its percentiles: that file is
# 7,300 s long and the load ends at about 3,800 s, which drags its KV median from
# 99.6% down to 9.0% if the whole file is used.
# The glob is passed as ONE quoted string: engine_occupancy.py expands --runs itself,
# and letting the shell expand it turns the extra paths into unrecognised arguments.
python3 "$R/engine_occupancy.py" --summary-only --window-s 3700 \
  --runs "results/*exp71${PASS}r1_*_full*" \
  --csv "$OUT/engine_layer.csv" 2>&1 | tail -2

echo
echo "=== done. Check the arm names and the point counts on what was drawn."
ls -1 "$OUT" | sed 's/^/  /'
