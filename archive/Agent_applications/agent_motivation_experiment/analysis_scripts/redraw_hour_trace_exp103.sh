#!/usr/bin/env bash
# EXP-103's hour trace: the first-token estimate stretched by the engine's
# measured prefill duty cycle, against the control it is read from. A snapshot
# of redraw_hour_trace_exp101.sh rather than an argument to it, for the reason
# that script gives about its own predecessors -- documents cite the output
# directories by name and a shared script would change their figures.
#
# Three series, and the third is the second repeat rather than a third arm,
# because the whole question here is whether the difference is larger than the
# repeat-to-repeat movement:
#
#   control            fsboth        EXP-101b, the same sixteen FluidServe
#                                    arguments; the two differ by exactly one
#                                    flag, checked against the archived specs
#   interleave rep 1   fsinterleave  EXP-103
#   interleave rep 2   fsinterleave  EXP-103
#
#   bash analysis_scripts/redraw_hour_trace_exp103.sh [out-dir]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp103_interleave}
PASS=${2:-}
R=analysis_scripts/request_level
mkdir -p "$OUT"

SH=$(ls -d results/*exp101br1_fsboth_shift 2>/dev/null | head -1)
CO=$(ls -d results/*exp103r1_fsinterleave_shift 2>/dev/null | head -1)
BO=$(ls -d results/*exp103r2_fsinterleave_shift 2>/dev/null | head -1)
NO=""
LD=""
echo "=== run selection (pass '${PASS:-1}')"
MISSING=0
for pair in "control fsboth (EXP-101b)|$SH" "interleave-aware rep 1|$CO" "interleave-aware rep 2|$BO"; do
  name=${pair%%|*}; dir=${pair#*|}
  if [ -n "$dir" ]; then printf "  %-22s %s\n" "$name" "$dir"
  else printf "  %-22s MISSING\n" "$name"; MISSING=1; fi
done
[ "$MISSING" = 1 ] && echo "  -> drawing with the arms that exist. This is NOT the full set."

echo
echo "=== engine attribution"
for d in "$SH" "$CO" "$BO"; do
  [ -n "$d" ] || continue
  [ -f "$d/analysis/request_engine.csv" ] && { echo "  $(basename "$d") already built"; continue; }
  python3 "$R/build_request_engine_map.py" "$d" 2>&1 | tail -1 | sed 's/^/  /'
done

SERIES=(); HOUR=(); RUNS=()
[ -n "$SH" ] && { SERIES+=("control (fsboth)|#17becf|-|$SH");   HOUR+=("control (fsboth)|#17becf|$SH");   RUNS+=("$SH"); }
[ -n "$CO" ] && { SERIES+=("interleave-aware rep 1|#d62728|-|$CO");     HOUR+=("interleave-aware rep 1|#d62728|$CO");     RUNS+=("$CO"); }
[ -n "$BO" ] && { SERIES+=("interleave-aware rep 2|#2ca02c|-|$BO");  HOUR+=("interleave-aware rep 2|#2ca02c|$BO");  RUNS+=("$BO"); }

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv \
  --knee 28.0 "FluidServe v0.2 on m1" \
  --mean-input 533 3814 6215 \
  --title "EXP-103 hour trace: the same Azure-shaped arrival band as EXP-93, 10.8-45.0 req/s, with the class mix stepping m2 A m1 B every 15 minutes so chat runs 93.0, 33.3, 76.9 and 60.0 percent of the requests. The knee is m1's measured 28.0 req/s; the other three segments have their own and none is measured." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline"
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT" \
  --title "EXP-103, the interleave-aware first-token estimate" --series "${SERIES[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# Naming the runs instead of globbing for them: the four arms come from three
# sessions, so no single pattern addresses the set, and `*r1_fspfx_shift*`
# matches exp98r1 and exp98br1 both -- drawing pass 1 with pass 2's engine data.
EV=()
[ -n "$NO" ] && true
[ -n "$SH" ] && EV+=("fsboth=$SH")
[ -n "$CO" ] && EV+=("fsinterleave=$CO")
[ -n "$BO" ] && EV+=("fsinterleave_r2=$BO")
[ -n "$LD" ] && true
python3 "$R/exp41_engine_view.py" --variant shift --out-dir "$OUT" \
  --run "${EV[@]}" --title "EXP-103" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the three arms side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "EXP-103 hour trace, the interleave-aware first-token estimate" \
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
