#!/usr/bin/env bash
# The static rate sweep with llm-d added as a fifth arm, drawn into EXP-66's own
# directory.
#
# Why a second script rather than an argument to `redraw_static_sweep.sh`. That
# script defines the canonical static sweep -- the four control planes that
# EXP-53 and EXP-57 measured -- and its output directory is cited by
# paper-outline.md and why-the-routing-layer.md. llm-d is not yet part of that
# comparison: it has one repeat where the others have two, it ran in a session
# five days later, and it is the only arm whose conditions were preceded by a
# three-minute pre-run that leaves the engines' prefix caches warm. Adding it to
# the canonical figures would put a line on them that the caveats do not fit
# beside. This script therefore writes to `exp66r1/` and leaves `exp53/` alone.
#
# The other four arms are the same runs `redraw_static_sweep.sh` selects, so the
# only difference between the two sets of figures is the extra line.
#
#   bash analysis_scripts/redraw_static_sweep_llmd.sh [out-dir] [llmd-glob]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp66r1}
LLMD=${2:-'results/*exp66r1_llmdslo_m1f_rpm_*'}
R=analysis_scripts/request_level
mkdir -p "$OUT"

LB='results/*exp53*_loadbalance_m1_rpm_*'
SLO='results/*exp53*_slo_m1f_rpm_*'
FS='results/*exp53*_fluidserve_m1_rpm_*'
PS='results/*exp57r*_polyserve_m1_rpm_*'     # superseded EXP-53 -> EXP-57

echo "=== run selection"
for pat in "$LB" "$SLO" "$FS" "$PS" "$LLMD"; do
  # shellcheck disable=SC2086
  printf '  %-46s %2d conditions\n' "$pat" "$(ls -d $pat 2>/dev/null | wc -l)"
done

echo
echo "=== per-request engine attribution for the llm-d conditions"
# llm-d does not go through the Llumnix scheduler, so the engine identity comes
# from Envoy's upstream-host field instead of the dispatch log. Rebuilt here
# rather than assumed present, and the matched fraction is printed: below 99%
# the per-engine figures are drawn on a subset.
# shellcheck disable=SC2086
python3 "$R/llmd_engine_map.py" --glob "$LLMD" || echo "  FAILED"

echo
echo "=== attainment and goodput against rate, five policies"
python3 "$R/exp53_compare.py" --runs "$LB" "$SLO" "$FS" "$PS" "$LLMD" --out "$OUT" \
  || echo "  FAILED"

echo
echo "=== goodput per class against rate"
python3 "$R/exp53_class_goodput.py" --sweep --runs "$LB" "$SLO" "$FS" "$PS" "$LLMD" \
  --out "$OUT" || echo "  FAILED"

echo
echo "=== engine layer, one directory for the llm-d arm"
# The other four arms already have theirs under exp53/engine_<arm>/ and are not
# redrawn here; nothing about them changed.
python3 "$R/plot_ratesweep_split.py" --glob "$LLMD" \
  --out-dir "$OUT/engine_llmd" >/dev/null 2>&1 \
  && echo "  llmd ok" || echo "  llmd FAILED"

echo
echo "=== the five policies side by side, one directory per rate"
for rpm in 2700 3300 4200; do
  python3 "$R/exp38_policy_compare.py" \
    --runs "results/*exp53*_loadbalance_m1_rpm_$rpm" \
           "results/*exp53*_slo_m1f_rpm_$rpm" \
           "results/*exp53*_fluidserve_m1_rpm_$rpm" \
           "results/*exp57r*_polyserve_m1_rpm_$rpm" \
           "${LLMD%_\*}_$rpm" \
    --out-dir "$OUT/side_by_side_$rpm" --title "EXP-53/57/66 $((rpm / 60)) req/s" \
    >/dev/null 2>&1 && echo "  $((rpm / 60)) req/s ok" || echo "  $((rpm / 60)) req/s FAILED"
done

echo
echo "=== per-engine class mix and separation, llm-d only"
python3 "$R/separation_measures.py" "$LLMD" \
  --csv "$OUT/separation_llmd.csv" || echo "  FAILED"

echo
echo "=== done. Check the arm names and point counts on what was drawn before"
echo "    quoting it -- a glob that matches nothing produces a figure with a"
echo "    missing line and no error (exp-plot skill, last section)."
