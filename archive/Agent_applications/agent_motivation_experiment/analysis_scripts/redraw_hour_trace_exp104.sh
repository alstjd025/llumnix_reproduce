#!/usr/bin/env bash
# EXP-104's hour trace: FluidServe v0.3 with the class preference on against off,
# two repeats each. A snapshot of redraw_hour_trace_exp103.sh rather than an
# argument to it, for the reason that script gives about its own predecessors.
#
# FOUR series, and they are two arms x two repeats rather than four arms. The
# question is whether the difference between the arms is larger than the movement
# between repeats of one arm, so both repeats have to be on the figure: the
# preference's answer already flipped once on this trace, costing 5.2 points
# before EXP-98's two changes and gaining 1.3 after them.
#
#   bash analysis_scripts/redraw_hour_trace_exp104.sh [out-dir]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp104_v03affinity}
PASS=${2:-}
R=analysis_scripts/request_level
mkdir -p "$OUT"

SH=$(ls -d results/*exp104r1_fsv3_shift 2>/dev/null | head -1)
CO=$(ls -d results/*exp104r2_fsv3_shift 2>/dev/null | head -1)
BO=$(ls -d results/*exp104r1_fsv3noaff_shift 2>/dev/null | head -1)
NO=$(ls -d results/*exp104r2_fsv3noaff_shift 2>/dev/null | head -1)
# The predecessor script blanked NO and LD here because it had no fourth or
# fifth series. Leaving those lines in would have wiped the run selected two
# lines above and dropped it from every panel without a word -- the same silent
# drop the arm tables abort on now.
LD=""
echo "=== run selection (pass '${PASS:-1}')"
MISSING=0
for pair in "v0.3, preference on rep 1|$SH" "v0.3, preference on rep 2|$CO" "v0.3, preference off rep 1|$BO" "v0.3, preference off rep 2|$NO"; do
  name=${pair%%|*}; dir=${pair#*|}
  if [ -n "$dir" ]; then printf "  %-22s %s\n" "$name" "$dir"
  else printf "  %-22s MISSING\n" "$name"; MISSING=1; fi
done
[ "$MISSING" = 1 ] && echo "  -> drawing with the arms that exist. This is NOT the full set."

echo
echo "=== engine attribution"
for d in "$SH" "$CO" "$BO" "$NO"; do
  [ -n "$d" ] || continue
  [ -f "$d/analysis/request_engine.csv" ] && { echo "  $(basename "$d") already built"; continue; }
  python3 "$R/build_request_engine_map.py" "$d" 2>&1 | tail -1 | sed 's/^/  /'
done

SERIES=(); HOUR=(); RUNS=()
[ -n "$SH" ] && { SERIES+=("v0.3 preference on, rep 1|#1f77b4|-|$SH");   HOUR+=("v0.3 preference on, rep 1|#1f77b4|$SH");   RUNS+=("$SH"); }
[ -n "$CO" ] && { SERIES+=("v0.3 preference on, rep 2|#17becf|-|$CO");     HOUR+=("v0.3 preference on, rep 2|#17becf|$CO");     RUNS+=("$CO"); }
[ -n "$NO" ] && { SERIES+=("v0.3 preference OFF, rep 2|#d62728|--|$NO");   HOUR+=("v0.3 preference OFF, rep 2|#d62728|$NO");   RUNS+=("$NO"); }
[ -n "$BO" ] && { SERIES+=("v0.3 preference OFF, rep 1|#ff7f0e|--|$BO");  HOUR+=("v0.3 preference OFF, rep 1|#ff7f0e|$BO");  RUNS+=("$BO"); }

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv \
  --knee 28.0 "FluidServe v0.2 on m1" \
  --mean-input 533 3814 6215 \
  --title "EXP-104 hour trace: the same Azure-shaped arrival band as EXP-93, 10.8-45.0 req/s, with the class mix stepping m2 A m1 B every 15 minutes so chat runs 93.0, 33.3, 76.9 and 60.0 percent of the requests. The knee is m1's measured 28.0 req/s; the other three segments have their own and none is measured." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline"
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT" \
  --title "EXP-104, v0.3 class preference on against off" --series "${SERIES[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# Naming the runs instead of globbing for them: the four arms come from three
# sessions, so no single pattern addresses the set, and `*r1_fspfx_shift*`
# matches exp98r1 and exp98br1 both -- drawing pass 1 with pass 2's engine data.
EV=()
[ -n "$NO" ] && true
[ -n "$SH" ] && EV+=("fsv3=$SH")
[ -n "$CO" ] && EV+=("fsv3_r2=$CO")
[ -n "$BO" ] && EV+=("fsv3noaff=$BO")
[ -n "$NO" ] && EV+=("fsv3noaff_r2=$NO")
[ -n "$LD" ] && true
python3 "$R/exp41_engine_view.py" --variant shift --out-dir "$OUT" \
  --run "${EV[@]}" --title "EXP-104" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the four runs side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "EXP-104 hour trace, v0.3 class preference on against off" \
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

echo
echo "=== what the decision predicted the first token would cost, against what it cost"
echo "    the gate: real/pred must reach 1.0 +/- 0.1, and it was 0.69 before this change"
python3 "$R/exp101_delay_accounting.py" --runs "${RUNS[@]}" 2>&1 | sed 's/^/  /'

echo
echo "=== separation, per class: how many instances each class runs on"
echo "    this is what the class preference is for, so it is the mechanism panel"
python3 "$R/separation_measures.py" "${RUNS[@]}" 2>&1 | tail -30
