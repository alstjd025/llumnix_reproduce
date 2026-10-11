#!/usr/bin/env bash
# EXP-98's hour trace: the per-instance correction and the pace cap in the memory
# predicate, drawn as a chain so each step is visible against the one before it.
# A snapshot of redraw_hour_trace_exp97.sh rather than an argument to it, for the
# reason that script gives about its own predecessors -- documents cite those
# output directories and a shared script would change their figures.
#
# Arms, in plain terms:
#   지금 배포 설정      fspfx    class preference ranks by the class's SHARE of
#                               that instance, which saturates at 1.0        EXP-97
#   정규화 고친 것      fscount  ranks by how MANY of the class it holds      EXP-97
#   거기에 이번 둘      fsboth   same, plus the correction kept per instance
#                               and the memory test reading min(capKv, capMem) EXP-98
#
# The two EXP-97 arms share a session with each other; the EXP-98 one does not
# share a session with either. That matters for reading small differences and not
# for the one this set is drawn to show: the segment gap is 22.5 points against a
# control repeat spread of 11.5.
#
# Runs are named rather than globbed. The three arms come from two sessions whose
# tags differ (exp97r1 / exp97br1 against exp98r1 / exp98r2), so no single pattern
# addresses the set, and `*r1_fspfx_shift*` matches exp97r1 AND exp97br1 both.
#
#   bash analysis_scripts/redraw_hour_trace_exp98.sh [out-dir] [pass]
#     pass: "" for the first repeat, "b" for the second
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp98_caps}
PASS=${2:-}
R=analysis_scripts/request_level
mkdir -p "$OUT"

if [ -z "$PASS" ]; then
  SH=$(ls -d results/*exp97r1_fspfx_shift 2>/dev/null | head -1)
  CO=$(ls -d results/*exp97r1_fscount_shift 2>/dev/null | head -1)
  BO=$(ls -d results/*exp98r1_fsboth_shift 2>/dev/null | head -1)
else
  SH=$(ls -d results/*exp97br1_fspfx_shift 2>/dev/null | head -1)
  CO=$(ls -d results/*exp97br1_fscount_shift 2>/dev/null | head -1)
  BO=$(ls -d results/*exp98r2_fsboth_shift 2>/dev/null | head -1)
fi
NO=""
LD=""
echo "=== run selection (pass '${PASS:-1}')"
MISSING=0
for pair in "deployed (share)|$SH" "normalisation fixed (count)|$CO" "plus per-inst. corr. + pace cap|$BO"; do
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
[ -n "$SH" ] && { SERIES+=("deployed (share)|#17becf|-|$SH");   HOUR+=("deployed (share)|#17becf|$SH");   RUNS+=("$SH"); }
[ -n "$CO" ] && { SERIES+=("normalisation fixed|#d62728|-|$CO");     HOUR+=("normalisation fixed|#d62728|$CO");     RUNS+=("$CO"); }
[ -n "$BO" ] && { SERIES+=("+ corr. + pace cap|#2ca02c|-|$BO");  HOUR+=("+ corr. + pace cap|#2ca02c|$BO");  RUNS+=("$BO"); }

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv \
  --knee 28.0 "FluidServe v0.2 on m1" \
  --mean-input 533 3814 6215 \
  --title "EXP-98 hour trace: the same Azure-shaped arrival band as EXP-93, 10.8-45.0 req/s, with the class mix stepping m2 A m1 B every 15 minutes so chat runs 93.0, 33.3, 76.9 and 60.0 percent of the requests. The knee is m1's measured 28.0 req/s; the other three segments have their own and none is measured." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline"
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT" \
  --title "EXP-98, class-preference normalisation" --series "${SERIES[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
# Naming the runs instead of globbing for them: the four arms come from three
# sessions, so no single pattern addresses the set, and `*r1_fspfx_shift*`
# matches exp98r1 and exp98br1 both -- drawing pass 1 with pass 2's engine data.
EV=()
[ -n "$NO" ] && true
[ -n "$SH" ] && EV+=("fspfx=$SH")
[ -n "$CO" ] && EV+=("fscount=$CO")
[ -n "$BO" ] && EV+=("fsboth=$BO")
[ -n "$LD" ] && true
python3 "$R/exp41_engine_view.py" --variant shift --out-dir "$OUT" \
  --run "${EV[@]}" --title "EXP-98" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the four arms side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "EXP-98 hour trace, class-preference normalisation" \
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
