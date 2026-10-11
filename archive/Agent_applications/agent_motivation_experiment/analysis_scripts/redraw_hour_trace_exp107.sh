#!/usr/bin/env bash
# EXP-107 hour trace: the class-instance cap and the force-branch removal over
# FluidServe v0.3, against the EXP-104 fsv3 control and the EXP-93 llm-d runs.
# A snapshot of redraw_hour_trace_exp105.sh rather than an argument to it.
#
# The set grows as the chain finishes: every arm is selected per repeat and a
# missing repeat is reported and skipped, so the same command redraws the full
# set after the chain and the interim set before it. Control and llm-d come
# from OTHER SESSIONS (2026-08-26 and 2026-08-22); the control's repeat spread
# is the floor for reading any difference, and llm-d additionally ran without a
# prewarm pass -- the same caveats EXP-93 recorded.
#
#   bash analysis_scripts/redraw_hour_trace_exp107.sh [out-dir]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
OUT=${1:-results/aggregate_analysis/exp107_capforce}
R=analysis_scripts/request_level
mkdir -p "$OUT"

C1=$(ls -d results/*exp104r1_fsv3_shift 2>/dev/null | head -1)
C2=$(ls -d results/*exp104r2_fsv3_shift 2>/dev/null | head -1)
K1=$(ls -d results/*exp107r1_fsv3cap_shift 2>/dev/null | head -1)
K2=$(ls -d results/*exp107r2_fsv3cap_shift 2>/dev/null | head -1)
F1=$(ls -d results/*exp107r1_fsv3nofrc_shift 2>/dev/null | head -1)
F2=$(ls -d results/*exp107r2_fsv3nofrc_shift 2>/dev/null | head -1)
B1=$(ls -d results/*exp107r1_fsv3capnofrc_shift 2>/dev/null | head -1)
B2=$(ls -d results/*exp107r2_fsv3capnofrc_shift 2>/dev/null | head -1)
G1=$(ls -d results/*exp107gr1_fsv3capg_shift 2>/dev/null | head -1)
G2=$(ls -d results/*exp107gr2_fsv3capg_shift 2>/dev/null | head -1)
H1=$(ls -d results/*exp107hr1_fsv3capgnofrc_shift 2>/dev/null | head -1)
H2=$(ls -d results/*exp107hr2_fsv3capgnofrc_shift 2>/dev/null | head -1)
L1=$(ls -d results/*exp93r1_llmdslo_shift 2>/dev/null | head -1)
L2=$(ls -d results/*exp93br1_llmdslo_shift 2>/dev/null | head -1)

# A directory exists from the moment its condition STARTS, but metrics.csv is
# merged only at the end -- drawing a run that is still in flight plots partial
# server series beside finished ones and crashes the client-side loaders. A
# header-only metrics.csv (~600 bytes) marks exactly that state, so require
# real content before a run may join the set.
complete() { [ -n "$1" ] && [ -s "$1/metrics.csv" ] && [ "$(wc -c < "$1/metrics.csv")" -gt 100000 ]; }
for v in C1 C2 K1 K2 F1 F2 B1 B2 G1 G2 H1 H2 L1 L2; do
  d=${!v}
  if [ -n "$d" ] && ! complete "$d"; then
    echo "  NOTE: $(basename "$d") is incomplete (metrics.csv not merged) -- skipped"
    eval "$v="
  fi
done

echo "=== run selection"
MISSING=0
for pair in "fsv3 control rep 1|$C1" "fsv3 control rep 2|$C2" \
            "fsv3cap rep 1|$K1" "fsv3cap rep 2|$K2" \
            "fsv3nofrc rep 1|$F1" "fsv3nofrc rep 2|$F2" \
            "fsv3capnofrc rep 1|$B1" "fsv3capnofrc rep 2|$B2" \
            "capg rep 1|$G1" "capg rep 2|$G2" \
            "capg+nofrc rep 1|$H1" "capg+nofrc rep 2|$H2" \
            "llm-d rep 1|$L1" "llm-d rep 2|$L2"; do
  name=${pair%%|*}; dir=${pair#*|}
  if [ -n "$dir" ]; then printf "  %-22s %s\n" "$name" "$dir"
  else printf "  %-22s MISSING\n" "$name"; MISSING=1; fi
done
[ "$MISSING" = 1 ] && echo "  -> drawing with the arms that exist. This is NOT the full set."

echo
echo "=== engine attribution"
for d in "$C1" "$C2" "$K1" "$K2" "$F1" "$F2" "$B1" "$B2" "$G1" "$G2" "$H1" "$H2" "$L1" "$L2"; do
  [ -n "$d" ] || continue
  [ -f "$d/analysis/request_engine.csv" ] && { echo "  $(basename "$d") already built"; continue; }
  python3 "$R/build_request_engine_map.py" "$d" 2>&1 | tail -1 | sed 's/^/  /'
done

SERIES=(); HOUR=(); RUNS=(); EV=()
[ -n "$C1" ] && { SERIES+=("fsv3 control rep 1|#1f77b4|-|$C1"); HOUR+=("fsv3 control rep 1|#1f77b4|$C1"); RUNS+=("$C1"); EV+=("fsv3=$C1"); }
[ -n "$C2" ] && { SERIES+=("fsv3 control rep 2|#17becf|-|$C2"); HOUR+=("fsv3 control rep 2|#17becf|$C2"); RUNS+=("$C2"); EV+=("fsv3_r2=$C2"); }
[ -n "$K1" ] && { SERIES+=("+instance cap rep 1|#9467bd|-|$K1"); HOUR+=("+instance cap rep 1|#9467bd|$K1"); RUNS+=("$K1"); EV+=("fsv3cap=$K1"); }
[ -n "$K2" ] && { SERIES+=("+instance cap rep 2|#c5b0d5|-|$K2"); HOUR+=("+instance cap rep 2|#c5b0d5|$K2"); RUNS+=("$K2"); EV+=("fsv3cap_r2=$K2"); }
[ -n "$F1" ] && { SERIES+=("force off rep 1|#2ca02c|--|$F1"); HOUR+=("force off rep 1|#2ca02c|$F1"); RUNS+=("$F1"); EV+=("fsv3nofrc=$F1"); }
[ -n "$F2" ] && { SERIES+=("force off rep 2|#98df8a|--|$F2"); HOUR+=("force off rep 2|#98df8a|$F2"); RUNS+=("$F2"); EV+=("fsv3nofrc_r2=$F2"); }
[ -n "$B1" ] && { SERIES+=("cap + force off rep 1|#d62728|-.|$B1"); HOUR+=("cap + force off rep 1|#d62728|$B1"); RUNS+=("$B1"); EV+=("fsv3capnofrc=$B1"); }
[ -n "$B2" ] && { SERIES+=("cap + force off rep 2|#ff9896|-.|$B2"); HOUR+=("cap + force off rep 2|#ff9896|$B2"); RUNS+=("$B2"); EV+=("fsv3capnofrc_r2=$B2"); }
[ -n "$G1" ] && { SERIES+=("guardrail cap rep 1|#7b3294|-|$G1"); HOUR+=("guardrail cap rep 1|#7b3294|$G1"); RUNS+=("$G1"); EV+=("fsv3capg=$G1"); }
[ -n "$G2" ] && { SERIES+=("guardrail cap rep 2|#c994c7|-|$G2"); HOUR+=("guardrail cap rep 2|#c994c7|$G2"); RUNS+=("$G2"); EV+=("fsv3capg_r2=$G2"); }
[ -n "$H1" ] && { SERIES+=("guardrail+force-off rep 1|#e6550d|-|$H1"); HOUR+=("guardrail+force-off rep 1|#e6550d|$H1"); RUNS+=("$H1"); EV+=("fsv3capgnofrc=$H1"); }
[ -n "$H2" ] && { SERIES+=("guardrail+force-off rep 2|#fdae6b|-|$H2"); HOUR+=("guardrail+force-off rep 2|#fdae6b|$H2"); RUNS+=("$H2"); EV+=("fsv3capgnofrc_r2=$H2"); }
[ -n "$L1" ] && { SERIES+=("llm-d rep 1|#8c564b|--|$L1"); HOUR+=("llm-d rep 1|#8c564b|$L1"); RUNS+=("$L1"); EV+=("llmdslo=$L1"); }
[ -n "$L2" ] && { SERIES+=("llm-d rep 2|#c49c94|--|$L2"); HOUR+=("llm-d rep 2|#c49c94|$L2"); RUNS+=("$L2"); EV+=("llmdslo_r2=$L2"); }

echo
echo "=== what the trace offered: rate and mix over the hour"
python3 traces/dynamic/plot_trace_shape.py \
  traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv \
  --knee 28.0 "FluidServe v0.2 on m1" \
  --mean-input 533 3814 6215 \
  --title "EXP-107 hour trace: Azure-shaped arrival band 10.8-45.0 req/s, class mix stepping m2 A m1 B every 15 minutes (chat 93.0, 33.3, 76.9, 60.0 percent of requests). The knee is m1's measured 28.0 req/s for v0.2; the other segments have their own and none is measured." \
  --out "$OUT/trace_shape.png" >/dev/null 2>&1 && echo "  ok" || echo "  FAILED"

echo
echo "=== fleet timeline"
python3 "$R/exp41_dynamic_timeline.py" --variant shift --out-dir "$OUT" \
  --title "EXP-107, class-instance cap and force-off against the v0.3 control and llm-d" \
  --series "${SERIES[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== engine layer over the hour"
python3 "$R/exp41_engine_view.py" --variant shift --out-dir "$OUT" \
  --run "${EV[@]}" --title "EXP-107" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== the runs side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" --title "EXP-107 hour trace, cap and force-off against control and llm-d" \
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
echo "=== separation, per class: how many instances each class runs on"
echo "    the cap's mechanism panel: gateTierCount is bounded, so N_eff should track demand"
python3 "$R/separation_measures.py" "${RUNS[@]}" \
  --csv "$OUT/separation_hour.csv" 2>&1 | tail -30
