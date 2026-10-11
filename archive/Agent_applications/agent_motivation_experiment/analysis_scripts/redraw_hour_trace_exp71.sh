#!/usr/bin/env bash
# The EXP-54 hour-trace figure set, drawn for EXP-71's two arms.
#
# Why a second script rather than an argument to redraw_hour_trace.sh. That one
# defines the canonical hour comparison -- the three Llumnix-side control planes
# on the 25-75 req/s trace -- and its output directory is cited by motivation.md.
# EXP-71 is a different trace (10-45 req/s, rebanded because EXP-70 put the knee
# at 28.1 req/s and 93% of the old band sat above it), a different workload (the
# per-worker prompt duplication was removed on 2026-08-08), and a different pair
# of arms. Putting them on the same figures would place four incomparable things
# on one axis.
#
#   FluidServe v0.2   arm `fspfx`, variant `fullb`
#   llm-d             arm `llmdslo`, variant `full`
#
# The two variant names differ because the arms come from different drivers:
# run_exp30_dynamic.sh names its variant and run_exp68_llmd.sh takes the session
# whole. Every script below is therefore given explicit directories, except
# exp41_engine_view.py which globs, and there the pattern carries a trailing
# wildcard so `_full` matches `_fullb` too.
#
# llm-d does not go through the Llumnix scheduler, so its engine attribution
# comes from Envoy's upstream-host field and has to be built before the engine
# figures can see it. That is the first step here rather than an assumption.
#
#   bash analysis_scripts/redraw_hour_trace_exp71.sh [out-dir] [pass]
#     pass: "" for the first (exp71r1), "b" for the second (exp71br1)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp71}
PASS=${2:-}
R=analysis_scripts/request_level
mkdir -p "$OUT"

FS_DIR=$(ls -d results/*exp71${PASS}r1_fspfx_fullb 2>/dev/null | head -1)
LD_DIR=$(ls -d results/*exp71${PASS}r1_llmdslo_full 2>/dev/null | head -1)

echo "=== run selection (pass '${PASS:-1}')"
for p in "$FS_DIR" "$LD_DIR"; do [ -n "$p" ] && echo "  $p" || echo "  MISSING"; done
[ -n "$FS_DIR" ] && [ -n "$LD_DIR" ] || { echo "ABORT: a run is missing"; exit 1; }

echo
echo "=== llm-d engine attribution from the Envoy access log"
python3 "$R/llmd_engine_map.py" "$LD_DIR" 2>&1 | tail -2

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_short_m123_b1045.csv \
  --knee 28.1 "FluidServe v0.2" --knee 18.7 "llm-d" \
  --mean-input 687 4538 6758 \
  --title "EXP-71 hour trace: Azure-shaped rate rebanded to 10-45 req/s, class mix stepping m1 m2 m3 m1 every 15 min. Mean input tokens are the measured 687 / 4,538 / 6,758. Knees are EXP-70's." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline, two policies"
python3 "$R/exp41_dynamic_timeline.py" --variant fullb --out-dir "$OUT" \
  --title "EXP-71" \
  --series "FluidServe v0.2|#17becf|-|$FS_DIR" \
           "llm-d|#8c564b|-|$LD_DIR" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# Trailing wildcard: the two arms carry different variant suffixes.
python3 "$R/exp41_engine_view.py" --variant full --out-dir "$OUT" \
  --pattern "results/*exp71${PASS}r1_{arm}_{variant}*" --title "EXP-71" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the two policies side by side"
python3 "$R/exp38_policy_compare.py" --runs "$FS_DIR" "$LD_DIR" \
  --out-dir "$OUT" --title "EXP-71 hour trace" \
  >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== goodput per class over the hour"
python3 "$R/exp53_class_goodput.py" --out "$OUT" \
  --hour "FluidServe v0.2|#17becf|$FS_DIR" \
         "llm-d|#8c564b|$LD_DIR" \
  --hour-name class_goodput_hour.png \
  >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== per-run engine, token and control-plane panels"
for d in "$FS_DIR" "$LD_DIR"; do
  python3 "$R/plot_ratesweep_split.py" --glob "$d" --out-dir "$OUT" \
    >/dev/null 2>&1 && echo "  $(basename "$d") ok" || echo "  $(basename "$d") FAILED"
done

echo
echo "=== separation: how many instances each class runs on, and the score beside it"
python3 "$R/separation_measures.py" "$FS_DIR" "$LD_DIR" \
  --csv "$OUT/separation_hour.csv" 2>&1 | tail -20

echo
echo "=== done. Check the arm names and the point counts on what was drawn."
ls -1 "$OUT" | sed 's/^/  /'
