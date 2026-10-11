#!/usr/bin/env bash
# Redraw the one-hour trace figures from the runs that are current.
#
# Same reason as redraw_static_sweep.sh. PolyServe's tier length table was
# corrected on 2026-08-05 and that arm alone re-measured as EXP-57, so its EXP-54
# hour runs are superseded. Every script here selects runs with a glob and
# EXP-54's glob still matches them; the ones that keep only the last match would
# pick the EXP-57 run by luck of the directory name sorting later, which is the
# kind of thing exp41_dynamic_timeline's own comment says to state rather than
# rely on. So the selection is written out.
#
#   PolyServe     EXP-57   (corrected tier table)
#   Llumnix SLO   EXP-54   (does not read that flag)
#   FluidServe    EXP-54   (does not read that flag)
#
# The load-balance arm is excluded. Its EXP-54 hour run stopped measuring the
# policy at minute 40, when it rejected nothing, saturated the fleet, and the
# load generator exhausted its ephemeral ports (implementation.md 58.4); what
# the engine series show after that is the client failing, not the policy.
#
# The EXP-56 hour runs are NOT here. They are the class-preference ablation --
# FluidServe against FluidServe with one flag off -- and belong to that
# experiment's own figures, not to the comparison between policies.
#
#   bash analysis_scripts/redraw_hour_trace.sh [out-dir]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp54}
R=analysis_scripts/request_level
mkdir -p "$OUT"

PS_DIR=$(ls -d results/*exp57r1_polyserve_full 2>/dev/null | head -1)
SLO_DIR=$(ls -d results/*exp54r1_slo_full 2>/dev/null | head -1)
FS_DIR=$(ls -d results/*exp54r1_fluidserve_full 2>/dev/null | head -1)

echo "=== run selection"
for p in "$FS_DIR" "$SLO_DIR" "$PS_DIR"; do
  [ -n "$p" ] && echo "  $p" || echo "  MISSING"
done
[ -n "$PS_DIR" ] && [ -n "$SLO_DIR" ] && [ -n "$FS_DIR" ] || { echo "ABORT: a run is missing"; exit 1; }
echo "  (excluded: $(ls -d results/*exp54r*_polyserve_full 2>/dev/null | wc -l) EXP-54 PolyServe hour runs on the stale tier table)"

echo
echo "=== fleet timeline, three policies"
python3 "$R/exp41_dynamic_timeline.py" --variant full --out-dir "$OUT" \
  --title "EXP-54/57" \
  --series "FluidServe|#1f77b4|-|$FS_DIR" \
           "Llumnix SLO|#2ca02c|-|$SLO_DIR" \
           "PolyServe|#d62728|-|$PS_DIR" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# This one has no --series, so both sessions are matched and the stale PolyServe
# runs are named in --exclude rather than left to sort order.
python3 "$R/exp41_engine_view.py" --variant full --out-dir "$OUT" \
  --pattern 'results/*exp5[47]r1_{arm}_{variant}' --title "EXP-54/57" \
  --exclude exp54r1_polyserve exp54r2_polyserve loadbalance \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the three policies side by side"
python3 "$R/exp38_policy_compare.py" \
  --runs "$FS_DIR" "$SLO_DIR" "$PS_DIR" \
  --out-dir "$OUT" --title "EXP-54/57 hour trace" \
  >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== goodput per class over the hour"
python3 "$R/exp53_class_goodput.py" --out "$OUT" \
  --hour "FluidServe|#1f77b4|$FS_DIR" \
         "Llumnix SLO|#2ca02c|$SLO_DIR" \
         "PolyServe|#d62728|$PS_DIR" \
  --hour-name class_goodput_hour.png \
  >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== per-run engine, token and control-plane panels"
# The standard per-condition set (exp-plot skill). Named by the run directory
# because an hour trace has no rpm in its name, so the PolyServe panels land
# under exp57r1 and the superseded exp54r1 ones have to be removed by hand.
for d in "$FS_DIR" "$SLO_DIR" "$PS_DIR"; do
  python3 "$R/plot_ratesweep_split.py" --glob "$d" --out-dir "$OUT" \
    >/dev/null 2>&1 && echo "  $(basename "$d") ok" || echo "  $(basename "$d") FAILED"
done

echo
echo "=== motivation figure 4 (selects per column in its own COLS table)"
python3 "$R/motivation_fig4.py" results/aggregate_analysis/motivation \
  >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== done. Check the arm names and point counts on what was drawn."
