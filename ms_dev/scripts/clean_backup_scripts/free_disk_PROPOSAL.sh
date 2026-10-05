#!/usr/bin/env bash
# PREPARED, NOT RUN. Deletes tbt_events.jsonl from runs that (a) no pinned
# manifest references and (b) are older than N days. Nothing else is touched.
#
#   bash free_disk_PROPOSAL.sh 3          dry run, keeps anything under 3 days old
#   bash free_disk_PROPOSAL.sh 3 --apply  actually delete
#
# tbt_events.jsonl is the per-token timing stream. It is 89% of results/ and is
# read by two ITL CDF figures and the within-request p99; per-token time itself
# comes from metrics.csv, which this never touches.
set -uo pipefail
DAYS=${1:-3}; APPLY=${2:-}
cd /home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment || exit 1
PIN=$(mktemp); trap 'rm -f "$PIN"' EXIT
grep -ohE '[0-9]{6}_[0-9]{4}_[A-Za-z0-9_]+' paper_experiment/*/manifest.tsv 2>/dev/null | sort -u > "$PIN"
echo "pinned runs: $(wc -l < "$PIN")"
n=0; bytes=0
while IFS= read -r f; do
  run=$(basename "$(dirname "$f")")
  grep -qxF "$run" "$PIN" && continue
  sz=$(stat -c%s "$f"); n=$((n+1)); bytes=$((bytes+sz))
  [ "$APPLY" = "--apply" ] && rm -f "$f"
done < <(find results -name tbt_events.jsonl -mtime +"$DAYS")
printf "%s %d files, %.0f GB\n" \
  "$([ "$APPLY" = "--apply" ] && echo deleted || echo 'would delete')" "$n" "$(echo "$bytes/1073741824" | bc -l)"
[ "$APPLY" = "--apply" ] && df -h / | tail -1
