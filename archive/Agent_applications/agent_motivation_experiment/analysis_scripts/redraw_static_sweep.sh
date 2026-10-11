#!/usr/bin/env bash
# Redraw every static rate-sweep figure from the runs that are current.
#
# Why this exists as a script rather than a list of commands in a document.
# Every figure script here selects its runs with a glob, and EXP-53's glob still
# matches PolyServe conditions that were superseded on 2026-08-05, when its tier
# length table was corrected and that arm alone was re-measured as EXP-57.
# Running the old command redraws the figures with the stale and the corrected
# PolyServe AVERAGED TOGETHER, and nothing in the output says so. The run
# selection therefore lives in one place, here, and every figure is drawn from
# it.
#
# Only PolyServe moved. --polyserve-tier-decode-tokens is read by that policy
# and no other, so the load-balance, Llumnix SLO and FluidServe arms keep their
# EXP-53 runs. That the two sessions can be joined was measured rather than
# assumed: Llumnix SLO, unchanged in code and configuration, was re-run in the
# EXP-57 session and read 52.3 and 21.7 offered at 45 and 70 req/s against
# EXP-53's 52.4 and 21.4, inside EXP-53's own repeat spread.
#
#   bash analysis_scripts/redraw_static_sweep.sh [out-dir]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp53}
R=analysis_scripts/request_level
mkdir -p "$OUT"

# One glob per arm. Written out rather than composed so that the next person to
# supersede an arm edits one line and sees the others unchanged.
LB='results/*exp53*_loadbalance_m1_rpm_*'
SLO='results/*exp53*_slo_m1f_rpm_*'
FS='results/*exp53*_fluidserve_m1_rpm_*'
PS='results/*exp57r*_polyserve_m1_rpm_*'     # superseded EXP-53 -> EXP-57

echo "=== run selection"
for pat in "$LB" "$SLO" "$FS" "$PS"; do
  # shellcheck disable=SC2086
  printf '  %-46s %2d conditions\n' "$pat" "$(ls -d $pat 2>/dev/null | wc -l)"
done
STALE=$(ls -d results/*exp53*_polyserve_m1_rpm_* 2>/dev/null | wc -l)
echo "  (excluded: $STALE EXP-53 PolyServe conditions on the stale tier table)"

echo
echo "=== attainment and goodput against rate, four policies"
python3 "$R/exp53_compare.py" --runs "$LB" "$SLO" "$FS" "$PS" --out "$OUT" || echo "  FAILED"

echo
echo "=== goodput per class against rate"
python3 "$R/exp53_class_goodput.py" --sweep --runs "$LB" "$SLO" "$FS" "$PS" \
  --out "$OUT" || echo "  FAILED"

echo
echo "=== engine layer, one directory per arm"
for arm in loadbalance slo fluidserve polyserve; do
  case $arm in
    loadbalance) g=$LB ;; slo) g=$SLO ;; fluidserve) g=$FS ;; polyserve) g=$PS ;;
  esac
  python3 "$R/plot_ratesweep_split.py" --glob "$g" \
    --out-dir "$OUT/engine_$arm" >/dev/null 2>&1 \
    && echo "  $arm ok" || echo "  $arm FAILED"
done

echo
echo "=== the four policies side by side, one directory per rate"
for rpm in 2700 3300 4200; do
  python3 "$R/exp38_policy_compare.py" \
    --runs "results/*exp53*_loadbalance_m1_rpm_$rpm" \
           "results/*exp53*_slo_m1f_rpm_$rpm" \
           "results/*exp53*_fluidserve_m1_rpm_$rpm" \
           "results/*exp57r*_polyserve_m1_rpm_$rpm" \
    --out-dir "$OUT/side_by_side_$rpm" --title "EXP-53/57 $((rpm / 60)) req/s" \
    >/dev/null 2>&1 && echo "  $((rpm / 60)) req/s ok" || echo "  $((rpm / 60)) req/s FAILED"
done

echo
echo "=== motivation figures 1 and 3 (they supersede internally, see their docstrings)"
MOT=results/aggregate_analysis/motivation
python3 "$R/motivation_fig1.py" "$MOT" >/dev/null 2>&1 && echo "  fig1 ok" || echo "  fig1 FAILED"
python3 "$R/motivation_fig3.py" "$MOT" >/dev/null 2>&1 && echo "  fig3 ok" || echo "  fig3 FAILED"

echo
echo "=== done. Check the arm names and point counts on what was drawn before"
echo "    quoting it -- a glob that matches nothing produces a figure with a"
echo "    missing line and no error (exp-plot skill, last section)."
