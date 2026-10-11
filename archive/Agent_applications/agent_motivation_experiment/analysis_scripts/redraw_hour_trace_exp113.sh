#!/usr/bin/env bash
# EXP-113: FluidServe on the mix-shift hour under Qwen2.5-72B, drawn beside the
# same arm's two Llama-3.1-70B repeats from EXP-109.
#
# WHY THE TWO ARE COMPARABLE AT ALL. They are not the same offered load. Qwen's
# answers are 1.46x longer on this workload and its decode step is 1.04x slower,
# so the same arrival rate is 1.52x the work; measured, FluidServe's 90% capacity
# crossing falls from 28.4 to 18.3 req/s (EXP-112, one repeat). The Qwen run
# therefore replays `shiftq`, the same hour thinned to 0.644 of its arrivals --
# that ratio -- which puts it at 0.95x of capacity on average against the
# original's 0.96x on Llama, with 44% of its minutes above the crossing against
# 45%. What the two series share is their POSITION RELATIVE TO CAPACITY, and
# that is the only thing they may be read as sharing. The Qwen line carries 63.4k
# requests against 98.0k, so any count is not comparable and only rates and
# fractions are.
#
# EVERY series is scored tok:7:75 -- the agent class promised TTFT <= 7 s AND a
# mean of <= 75 ms per token. On Qwen that promise means an average of 82.5 s per
# swe request against 44.0 s on Llama, because the promise is about pace and the
# answers got longer. Say that wherever these figures are used.
#
#   bash analysis_scripts/redraw_hour_trace_exp113.sh [out-dir]
set -uo pipefail
cd "$(dirname "$0")/.."
R=analysis_scripts/request_level
OUT=${1:-results/aggregate_analysis/exp113_hour_qwen}
mkdir -p "$OUT"

pick() {  # newest non-PRERUN, merged run matching $1
  local d
  for d in $(ls -dt $1 2>/dev/null | grep -v PRERUN); do
    [ -d "$d/shards" ] && { echo "  skip (unmerged, shards/ present): $(basename "$d")" >&2; continue; }
    [ "$(wc -l < "$d/metrics.csv" 2>/dev/null || echo 1)" -gt 1 ] || { echo "  skip (metrics.csv header only): $(basename "$d")" >&2; continue; }
    echo "$d"; return 0
  done
  return 0
}
# Two figure sets, because they answer two questions and a reader cannot tell
# them apart if they share a canvas:
#   A. the five policies on the SAME thinned hour -- the policy comparison.
#   B. FluidServe on Qwen beside FluidServe on Llama -- the model comparison,
#      and the only set where the series deliberately replay different traces.
for rep in 1 2; do
  eval "Q_FS$rep=\$(pick 'results/*exp113r${rep}_fsv3capgnofrct75_shiftq')"
  eval "Q_PS$rep=\$(pick 'results/*exp113r${rep}_polyservept75_shiftq')"
  eval "Q_SL$rep=\$(pick 'results/*exp113r${rep}_slot75_shiftq')"
  eval "Q_VL$rep=\$(pick 'results/*exp113r${rep}_vllmcachet75_shiftq')"
  eval "Q_LD$rep=\$(pick 'results/*exp113r${rep}_llmdslot75_shiftq')"
done
Q_FS=$Q_FS1
L1=$(pick 'results/*exp109r1_fsv3capgnofrct75_shift')
L2=$(pick 'results/*exp109r2_fsv3capgnofrct75_shift')

echo "=== run selection"
for pair in "Qwen FluidServe r1|$Q_FS1" "Qwen FluidServe r2|$Q_FS2" \
            "Qwen PolyServe r1|$Q_PS1" "Qwen PolyServe r2|$Q_PS2" \
            "Qwen Llumnix SLO r1|$Q_SL1" "Qwen Llumnix SLO r2|$Q_SL2" \
            "Qwen vLLM router r1|$Q_VL1" "Qwen vLLM router r2|$Q_VL2" \
            "Qwen llm-d r1|$Q_LD1" "Qwen llm-d r2|$Q_LD2" \
            "Llama FluidServe r1|$L1" "Llama FluidServe r2|$L2"; do
  n=${pair%%|*}; d=${pair#*|}
  [ -n "$d" ] && printf "  %-20s %s\n" "$n" "$(basename "$d")" || printf "  %-20s MISSING\n" "$n"
done
[ -n "$Q_FS" ] || { echo "ABORT: our arm is missing"; exit 1; }

echo
echo "=== engine attribution (per-request -> engine, built once per run)"
for d in "$Q_FS1" "$Q_FS2" "$Q_PS1" "$Q_PS2" "$Q_SL1" "$Q_SL2" \
           "$Q_VL1" "$Q_VL2" "$Q_LD1" "$Q_LD2" "$L1" "$L2"; do
  [ -n "$d" ] || continue
  [ -f "$d/analysis/request_engine.csv" ] && continue
  python3 "$R/build_request_engine_map.py" "$d" >/dev/null 2>&1 \
    && echo "  built $(basename "$d")" || echo "  FAILED $(basename "$d")"
done

# ---------------------------------------------------------------- set A: policies
SERIES=(); HOUR=(); RUNS=(); EV=()
add() {  # label colour style dir [arm-key]
  [ -n "$4" ] || return 0
  SERIES+=("$1|$2|$3|$4|tok:7:75"); HOUR+=("$1|$2|$4"); RUNS+=("$4")
  [ -n "${5:-}" ] && EV+=("$5=$4")
  return 0
}
# Arm colours are the repository's fixed ones so a colour means the same policy
# in every figure.
# Both repeats are drawn, the second in the lighter shade of the same hue, so a
# reader sees the between-repeat movement instead of an average that hides it.
# The arm KEY for the engine-layer figure goes to repeat 1 only: that figure
# takes one run per arm.
add "FluidServe r1"  "#1f77b4" "-"  "$Q_FS1" fsv3capgnofrct75
add "FluidServe r2"  "#aec7e8" "-"  "$Q_FS2" ""
add "llm-d r1"       "#8c564b" "--" "$Q_LD1" llmdslot75
add "llm-d r2"       "#c49c94" "--" "$Q_LD2" ""
add "PolyServe r1"   "#d62728" "-." "$Q_PS1" polyservept75
add "PolyServe r2"   "#ff9896" "-." "$Q_PS2" ""
add "Llumnix SLO r1" "#2ca02c" ":"  "$Q_SL1" slot75
add "Llumnix SLO r2" "#98df8a" ":"  "$Q_SL2" ""
add "vLLM router r1" "#7f7f7f" "-"  "$Q_VL1" vllmcachet75
add "vLLM router r2" "#c7c7c7" "-"  "$Q_VL2" ""

echo
echo "=== [A] in-flight-at-end, which decides where every series must be cut"
python3 "$R/in_flight_at_end.py" "${RUNS[@]}" 2>&1 | tail -10

echo
echo "=== [A] attainment and rate over the hour, five policies"
python3 "$R/exp41_dynamic_timeline.py" --variant shiftq --out-dir "$OUT" --cut-min 60 \
  --title "EXP-113: five control planes on the thinned mix-shift hour, Qwen2.5-72B, two repeats each; agent class promised TTFT 7 s + 75 ms/token" \
  --series "${SERIES[@]}" && echo "  ok" || echo "  FAILED"

echo
echo "=== [A] engine layer over the hour (one run per arm key)"
python3 "$R/exp41_engine_view.py" --variant shiftq --out-dir "$OUT" \
  --run "${EV[@]}" --title "EXP-113 Qwen2.5-72B" && echo "  ok" || echo "  FAILED"

echo
echo "=== [A] policies side by side"
python3 "$R/exp38_policy_compare.py" --runs "${RUNS[@]}" --tag hour \
  --out-dir "$OUT" && echo "  ok" || echo "  FAILED"

echo
echo "=== [A] per-class goodput over the hour"
python3 "$R/exp53_class_goodput.py" --out "$OUT" --hour "${HOUR[@]}" \
  && echo "  ok" || echo "  FAILED"

echo
echo "=== [A] per-condition engine panels"
for d in "${RUNS[@]}"; do
  python3 "$R/plot_ratesweep_split.py" --glob "$d" --out-dir "$OUT" >/dev/null 2>&1 \
    && echo "  ok $(basename "$d")" || echo "  FAILED $(basename "$d")"
done

echo
echo "=== [A] class separation (windowed; a pooled number cannot see a moving target)"
python3 "$R/separation_measures.py" "${RUNS[@]}" 2>&1 | tail -12

# ------------------------------------------------------- set B: the two models
# Our arm only, on two traces. Panel A of this figure is the one place in the set
# where the series are SUPPOSED to differ, and the script now says so in the
# title instead of asserting they are the same.
if [ -n "$L1" ] || [ -n "$L2" ]; then
  MSER=("FluidServe on Qwen2.5-72B r1|#1f77b4|-|$Q_FS1|tok:7:75")
  [ -n "$Q_FS2" ] && MSER+=("FluidServe on Qwen2.5-72B r2|#aec7e8|-|$Q_FS2|tok:7:75")
  [ -n "$L1" ] && MSER+=("FluidServe on Llama-3.1-70B rep 1|#d62728|--|$L1|tok:7:75")
  [ -n "$L2" ] && MSER+=("FluidServe on Llama-3.1-70B rep 2|#ff9896|--|$L2|tok:7:75")
  echo
  echo "=== [B] the same policy on two models, each at the same fraction of its own capacity"
  python3 "$R/exp41_dynamic_timeline.py" --variant shiftq --out-dir "$OUT/models" --cut-min 60 \
    --title "EXP-113: FluidServe on Qwen2.5-72B (hour thinned to 0.644) beside Llama-3.1-70B (EXP-109, unthinned); both at ~0.95x of their own 90% capacity" \
    --series "${MSER[@]}" && echo "  ok" || echo "  FAILED"
fi

echo
echo "=== wrote into $OUT"
ls -1 "$OUT" | sed 's/^/  /'
