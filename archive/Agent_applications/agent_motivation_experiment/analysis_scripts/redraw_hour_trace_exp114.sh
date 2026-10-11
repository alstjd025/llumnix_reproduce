#!/usr/bin/env bash
# EXP-114. FluidServe against llm-d on the mix-shift hour, EIGHT Llama-3.1-8B
# instances (TP=1, --api-server-count 4, gateway -v 0), one repeat each.
#
#   FluidServe  fsv3capgnofrct75dl -- the v0.4 candidate WITH the first-token
#               deadline in the feasibility test (EXP-114 section 13.24)
#   llm-d       predicted-latency routing behind its own EPP
#
# ⚠ FOUR THINGS THIS SET CANNOT BE READ WITHOUT.
#
# 1. THE TRACE IS SCALED 5.00x, and that is not the scaling that would match the
#    Llama-70B hour. The hour was built for a fleet whose knee is 28.0 req/s;
#    this fleet's is 173.5, so the same relative position needs 6.20x. At 6.20x
#    the load generator could not drive the arm that admits -- FluidServe was
#    offered 50-68% of the schedule through the peak while llm-d, refusing over
#    80% and therefore holding a quarter of the concurrent streams, was offered
#    100%. 5.00x puts the band at 0.28x-1.30x the knee, mean 0.78x, 25% of
#    minutes above it, against 0.35x-1.61x / 0.97x / 45% for the 70B hour.
#    Any comparison with the 70B or Qwen hour carries that difference.
#
# 2. BOTH ARMS DELIVERED EVERY MINUTE at 5.00x with 36 worker processes each:
#    none under 85% of schedule, worst minute 0.95, 496,210 arrivals against the
#    trace's 496,210. Streams per worker 115 and 29, below the ~150 where a
#    CPython worker saturates. That check is what makes this pair readable and
#    the 6.20x pair not.
#
# 3. THE SCORED PER-TOKEN TIME IS STILL CONTAMINATED, asymmetrically. Worker
#    freeze survives complete delivery, and it is larger in the arm that admits
#    more (115 streams per worker against 29). The raw figures therefore lean
#    AGAINST FluidServe. Read them beside the corrected pass.
#
# 4. ONE REPEAT PER ARM. No error bars anywhere in this set.
#
# llm-d's engine attribution comes from Envoy's access log, not the scheduler's
# dispatch log, which holds nothing for an arm that never passes through the
# Llumnix scheduler.
#
#   bash analysis_scripts/redraw_hour_trace_exp114.sh [out-dir]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp114_hour}
PASS=${2:-}
R=analysis_scripts/request_level
mkdir -p "$OUT"

BO=$(ls -d results/*exp114h50r1_fsv3capgnofrct75dl_shift50 2>/dev/null | head -1)
# ⚠ r2, not r1. The r1 llm-d hour was served by only four of the eight engines:
# the InferencePool the EPP resolves endpoints from (--pool-name=llmd-engines)
# still carried the four ports of the previous fleet shape, so 8004-8007 took
# zero requests while the FluidServe hour an hour earlier used all eight. The
# driver reported "verified: llmd-endpoints has 8 endpoints" throughout,
# because that check reads the file-discovery ConfigMap, which this
# deployment does not route on. r2 is the rerun after the InferencePool was
# patched, and all eight engines served (12,167 to 43,540 requests each).
LD=$(ls -d results/*exp114h50r2_llmdslot75_shift50 2>/dev/null | head -1)
SH=""
CO=""
NO=""
echo "=== run selection (pass '${PASS:-1}')"
MISSING=0
for pair in "FluidServe (deadline)|$BO" "llm-d|$LD"; do
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
[ -n "$BO" ] && { SERIES+=("FluidServe (deadline)|#1f77b4|-|$BO"); HOUR+=("FluidServe (deadline)|#1f77b4|$BO"); RUNS+=("$BO"); }
[ -n "$LD" ] && { SERIES+=("llm-d|#8c564b|--|$LD");               HOUR+=("llm-d|#8c564b|$LD");               RUNS+=("$LD"); }

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045_x500.csv \
  --knee 173.5 "FluidServe on eight 8B instances" \
  --mean-input 533 3814 6215 \
  --title "EXP-114 hour trace, the same mix-shift hour upscaled 5.00x for eight Llama-3.1-8B instances: 49-225 req/s against a measured knee of 173.5, so 0.28x-1.30x with a 0.78x mean and 25 percent of minutes above. The class mix still steps m2 A m1 B every 15 minutes. 6.20x would have matched the Llama-70B hour position but the load generator could not drive it." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline"
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT" \
  --title "EXP-114: eight Llama-3.1-8B instances, hour trace at 5.00x" --series "${SERIES[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# Naming the runs instead of globbing for them: the four arms come from three
# sessions, so no single pattern addresses the set, and `*r1_fspfx_shift*`
# matches exp98r1 and exp98br1 both -- drawing pass 1 with pass 2's engine data.
EV=()
[ -n "$NO" ] && true
[ -n "$BO" ] && EV+=("fsdl=$BO")
[ -n "$LD" ] && EV+=("llmdslo=$LD")
[ -n "$LD" ] && true
python3 "$R/exp41_engine_view.py" --variant shift --out-dir "$OUT" \
  --run "${EV[@]}" --title "FluidServe vs llm-d" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the four arms side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "EXP-114 hour trace, eight 8B instances, 5.00x" \
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
