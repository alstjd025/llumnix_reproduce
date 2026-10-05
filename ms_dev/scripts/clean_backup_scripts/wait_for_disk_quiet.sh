#!/bin/bash
# Emit one line and exit when the tbt archiving has either reached its target or
# gone quiet. Waiting matters because the archive is a copy-and-verify across two
# NVMe devices on the same node the engines, gateway and runner share, and this
# project has already had a node-level load source (per-token gateway logging)
# change the quantity the SLO is scored on.
R=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment/results
prev=-1; same=0; i=0
while [ $i -lt 200 ]; do
  free=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
  n=$(find "$R" -name tbt_events.jsonl 2>/dev/null | wc -l)
  if [ "$free" -ge 850 ]; then echo "DISK TARGET REACHED: ${free}G free, $n tbt files left"; exit 0; fi
  if [ "$n" = "$prev" ]; then same=$((same+1)); else same=0; fi
  prev=$n
  if [ "$same" -ge 4 ]; then echo "DISK QUIET: ${free}G free, $n tbt files left, unchanged for 4 checks"; exit 0; fi
  i=$((i+1)); sleep 45
done
echo "DISK WAIT TIMED OUT: $(df --output=avail -BG / | tail -1 | tr -dc 0-9)G free"
