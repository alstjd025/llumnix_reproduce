#!/usr/bin/env bash
# The current FluidServe against llm-d on the mix-shift hour trace, and nothing
# else. A snapshot of redraw_hour_trace_exp98.sh with the intermediate arms
# removed: that set is for reading the chain of changes, this one is for reading
# the two systems, and a figure carrying both questions answers neither well.
#
#   FluidServe (current)  class preference by count, correction per instance,
#                         memory predicate reading min(capKv, capMem)     EXP-98
#   llm-d                 predicted-latency routing behind its own EPP    EXP-93
#
# The two do not share a session. The differences this set is drawn for are far
# outside the repeat spread of either (26.5 points of rejection, 25.9 of
# attainment against spreads of 1.0 and 0.9), so that does not invalidate them,
# but it is stated here because smaller differences in the same figures would
# not survive it.
#
# llm-d's engine attribution comes from Envoy's access log, not the scheduler's
# dispatch log, which holds nothing for an arm that does not pass through the
# Llumnix scheduler. llmd_engine_map does that join and reaches 99.94% here.
#
#   bash analysis_scripts/redraw_hour_trace_ours_vs_llmd.sh [out-dir] [pass]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/ours_vs_llmd_hour}
PASS=${2:-}
R=analysis_scripts/request_level
mkdir -p "$OUT"

if [ -z "$PASS" ]; then
  BO=$(ls -d results/*exp98r1_fsboth_shift 2>/dev/null | head -1)
  LD=$(ls -d results/*exp93r1_llmdslo_shift 2>/dev/null | head -1)
else
  BO=$(ls -d results/*exp98r2_fsboth_shift 2>/dev/null | head -1)
  LD=$(ls -d results/*exp93br1_llmdslo_shift 2>/dev/null | head -1)
fi
SH=""
CO=""
NO=""
echo "=== run selection (pass '${PASS:-1}')"
MISSING=0
for pair in "FluidServe (current)|$BO" "llm-d|$LD"; do
  name=${pair%%|*}; dir=${pair#*|}
  if [ -n "$dir" ]; then printf "  %-22s %s\n" "$name" "$dir"
  else printf "  %-22s MISSING\n" "$name"; MISSING=1; fi
done
[ "$MISSING" = 1 ] && echo "  -> drawing with the arms that exist. This is NOT the full set."

echo
echo "=== engine attribution"
for d in "$BO"; do
  [ -n "$d" ] || continue
  [ -f "$d/analysis/request_engine.csv" ] && { echo "  $(basename "$d") already built"; continue; }
  python3 "$R/build_request_engine_map.py" "$d" 2>&1 | tail -1 | sed 's/^/  /'
done

SERIES=(); HOUR=(); RUNS=()
[ -n "$BO" ] && { SERIES+=("FluidServe (current)|#1f77b4|-|$BO"); HOUR+=("FluidServe (current)|#1f77b4|$BO"); RUNS+=("$BO"); }
[ -n "$LD" ] && { SERIES+=("llm-d|#8c564b|--|$LD");               HOUR+=("llm-d|#8c564b|$LD");               RUNS+=("$LD"); }

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv \
  --knee 28.0 "FluidServe v0.2 on m1" \
  --mean-input 533 3814 6215 \
  --title "FluidServe vs llm-d hour trace: the same Azure-shaped arrival band as EXP-93, 10.8-45.0 req/s, with the class mix stepping m2 A m1 B every 15 minutes so chat runs 93.0, 33.3, 76.9 and 60.0 percent of the requests. The knee is m1's measured 28.0 req/s; the other three segments have their own and none is measured." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline"
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT" \
  --title "FluidServe vs llm-d, class-preference normalisation" --series "${SERIES[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# Naming the runs instead of globbing for them: the four arms come from three
# sessions, so no single pattern addresses the set, and `*r1_fspfx_shift*`
# matches exp98r1 and exp98br1 both -- drawing pass 1 with pass 2's engine data.
EV=()
[ -n "$NO" ] && true
[ -n "$BO" ] && EV+=("fsboth=$BO")
[ -n "$LD" ] && EV+=("llmdslo=$LD")
[ -n "$LD" ] && true
python3 "$R/exp41_engine_view.py" --variant shift --out-dir "$OUT" \
  --run "${EV[@]}" --title "FluidServe vs llm-d" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the four arms side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "FluidServe vs llm-d hour trace, class-preference normalisation" \
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
