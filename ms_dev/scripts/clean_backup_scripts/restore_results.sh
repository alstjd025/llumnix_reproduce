#!/usr/bin/env bash
# 2026-08-31 복원: results_priority (151 GB) -> 실험 저장소.
# RESTORE-2026-08-28.md §2의 마지막 네 줄과 같은 순서.
set -uo pipefail
B=/NHNHOME/NXC_ROOT/NXC13/home_backup_2026-08-28
A=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
mkdir -p "$A/results"

echo "[$(date '+%F %T')] 1/3 paper_experiment (1.7G)"
rsync -a --stats "$B/results_priority/paper_experiment" "$A/" 2>&1 | tail -5

echo "[$(date '+%F %T')] 2/3 aggregate_analysis (865M)"
rsync -a --stats "$B/results_priority/aggregate_analysis" "$A/results/" 2>&1 | tail -5

echo "[$(date '+%F %T')] 3/3 results (1,441 run, 약 149G, 파일 550만)"
rsync -a --stats "$B/results_priority/results/" "$A/results/" 2>&1 | tail -8

echo "[$(date '+%F %T')] DONE rc=$?"
du -sh "$A/results" "$A/paper_experiment"
