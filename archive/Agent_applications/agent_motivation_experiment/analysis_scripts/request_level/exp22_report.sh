#!/bin/bash
# Post-process an EXP-22 pair of runs and produce every artefact in one go.
#
#   ./exp22_report.sh <out-dir> <run-dir> [<run-dir> ...]
#
# Per run: attribute each request to the engine that served it (needed for the
# routing-concentration measure), check that the fleet was used as a fleet, then
# report what the controller did from the scraped scheduler series. Across runs: attainment against both denominators,
# goodput, the time course, and the hour re-expressed as a rate sweep.
set -uo pipefail
OUT=${1:?usage: exp22_report.sh <out-dir> <run-dir>...}
shift
# Resolve everything against the caller's directory before moving to the script's
# own, or relative run paths silently resolve to nothing that exists and the
# report comes out empty rather than failing.
OUT=$(readlink -f "$OUT")
RUNS=()
for r in "$@"; do RUNS+=("$(readlink -f "$r")"); done
cd "$(dirname "$0")"
[ ${#RUNS[@]} -gt 0 ] || { echo "no run dirs given"; exit 1; }

mkdir -p "$OUT"
for r in "${RUNS[@]}"; do
  echo "=== $(basename "$r") ==="
  python3 build_request_engine_map.py "$r" 2>&1 | tail -3
  # Engine occupancy first, because it is the one artefact that distinguishes a
  # run that was merely mediocre from one where the router committed thousands
  # of requests to a single engine while the others idled. That failure is
  # invisible in the request-level numbers: the requests behind the queue never
  # finish, so they are dropped as "still in flight" rather than counted.
  python3 engine_occupancy.py --run "$r" --summary-only 2>&1 | tail -8
  # Then the two SLO rules per class, separately. Combining them into one
  # attainment figure hides the difference between "the tokens came out too
  # slowly", which is a routing outcome, and "the first token never arrived",
  # which usually is not.
  python3 slo_rule_breakdown.py --runs "$r" 2>&1 | tail -25
  python3 exp22_controller.py --run "$r" --out-dir "$OUT" 2>&1 | tail -40
done

echo
echo "=== across arms ==="
python3 exp22_fluidserve.py --runs "${RUNS[@]}" --out-dir "$OUT" | tee "$OUT/summary.txt"
echo
echo "artefacts in $OUT:"
ls -la "$OUT"
