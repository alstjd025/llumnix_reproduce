#!/usr/bin/env bash
# Index and dated record of every move/deletion: README.md in this directory.
#
# Move tbt_events.jsonl and agent_logs/ of every run from the node's local disk to
# the Lustre backup, zstd-compressed, verified byte for byte before anything is
# removed. 2026-10-05, user's instruction ("압축해서 옮기는게 좋을것같아").
#
#   archive   compress + verify every item, write MANIFEST.tsv. Removes nothing.
#   delete    for every item MANIFEST.tsv marks OK, re-check that the source is
#             unchanged and remove it. Writes DELETED.tsv.
#
# Re-running either phase skips what is already done, so an interrupted run can
# simply be started again.
#
# Verification
#   tbt_events : sha256 of the original file == sha256 of the archive decompressed
#                back from Lustre.
#   agent_logs : `tar --diff` of the archive (read back from Lustre) against the
#                original directory -- compares every file's contents, size and
#                mtime -- plus file count and byte total from both sides.
# An archive is written as *.part and renamed only after verification passes.
set -uo pipefail

SRC=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment/results
DST=/NHNHOME/NXC_ROOT/NXC13/home_backup_2026-08-28/results_archive_2026-10-05
MAN=$DST/MANIFEST.tsv
DEL=$DST/DELETED.tsv
LOG=$DST/archive.log
# A copy of MANIFEST/DELETED/log is kept next to this script, so the record of
# what moved lives in one place in the repository (README.md here is the index).
REC=/home/nxclab/llumnix_reproduce/ms_dev/scripts/clean_backup_scripts/results_archive_2026-10-05
JOBS=${JOBS:-8}
trap 'mkdir -p "$REC"; cp -p "$MAN" "$DEL" "$LOG" "$REC/" 2>/dev/null' EXIT
export SRC DST MAN DEL LOG

mkdir -p "$DST/tbt_events" "$DST/agent_logs"
[ -f "$MAN" ] || printf 'kind\trun\tsrc_path\tsrc_bytes\tsrc_files\tsrc_sha256\tarchive_path\tarchive_bytes\tstatus\tverified_at\n' > "$MAN"
[ -f "$DEL" ] || printf 'kind\trun\tsrc_path\tsrc_bytes\tdeleted_at\n' > "$DEL"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }
row() { ( flock 9; printf '%s\n' "$1" >> "$MAN" ) 9>"$MAN.lock"; }
done_ok() { awk -F'\t' -v k="$1" -v r="$2" '$1==k && $2==r && $9=="OK" {f=1} END{exit !f}' "$MAN"; }
export -f log row done_ok

do_tbt() {
  local run=$1 src=$SRC/$1/tbt_events.jsonl out=$DST/tbt_events/$1.tbt_events.jsonl.zst
  done_ok tbt "$run" && return 0
  local bytes h1 h2 ab
  bytes=$(stat -c %s "$src")
  h1=$(sha256sum < "$src" | cut -d' ' -f1)
  if ! zstd -q -9 -T8 -f "$src" -o "$out.part"; then log "FAIL compress tbt $run"; return 1; fi
  h2=$(zstd -dc "$out.part" | sha256sum | cut -d' ' -f1)
  if [ "$h1" != "$h2" ]; then
    log "FAIL verify tbt $run $h1 != $h2"
    row "tbt	$run	$src	$bytes	1	$h1	$out.part	-	FAIL	$(date '+%F %T')"; return 1
  fi
  mv -f "$out.part" "$out"; ab=$(stat -c %s "$out")
  row "tbt	$run	$src	$bytes	1	$h1	$out	$ab	OK	$(date '+%F %T')"
  log "OK tbt $run $bytes -> $ab"
}

do_alog() {
  local run=$1 dir=$SRC/$1/agent_logs out=$DST/agent_logs/$1.agent_logs.tar.zst
  done_ok alog "$run" && return 0
  local n b nt bt ab h
  read -r n b < <(find "$dir" -type f -printf '%s\n' | awk '{n++; s+=$1} END{print n+0, s+0}')
  if ! tar -C "$SRC/$run" -cf - agent_logs | zstd -q -9 -T4 -f -o "$out.part"; then log "FAIL compress alog $run"; return 1; fi
  read -r nt bt < <(zstd -dc "$out.part" | tar -tvf - | awk '$1 ~ /^-/ {n++; s+=$3} END{print n+0, s+0}')
  if [ "$n" != "$nt" ] || [ "$b" != "$bt" ] || ! zstd -dc "$out.part" | tar -C "$SRC/$run" -df - >/dev/null 2>>"$LOG"; then
    log "FAIL verify alog $run src=$n/$b archive=$nt/$bt"
    row "alog	$run	$dir	$b	$n	-	$out.part	-	FAIL	$(date '+%F %T')"; return 1
  fi
  mv -f "$out.part" "$out"; ab=$(stat -c %s "$out")
  h=$(sha256sum < "$out" | cut -d' ' -f1)   # hash of the archive itself, for later integrity checks
  row "alog	$run	$dir	$b	$n	$h	$out	$ab	OK	$(date '+%F %T')"
  log "OK alog $run $n files $b -> $ab"
}
export -f do_tbt do_alog

case "${1:-}" in
archive)
  log "=== archive start (JOBS=$JOBS)"
  find "$SRC" -mindepth 2 -maxdepth 2 -name tbt_events.jsonl -printf '%h\n' | xargs -n1 basename | sort \
    | xargs -P "$JOBS" -I{} bash -c 'do_tbt "$@"' _ {}
  log "=== tbt phase done"
  find "$SRC" -mindepth 2 -maxdepth 2 -name agent_logs -type d -printf '%h\n' | xargs -n1 basename | sort \
    | xargs -P "$JOBS" -I{} bash -c 'do_alog "$@"' _ {}
  log "=== archive done: OK $(awk -F'\t' '$9=="OK"' "$MAN" | wc -l), FAIL $(awk -F'\t' '$9=="FAIL"' "$MAN" | wc -l)"
  ;;
delete)
  log "=== delete start"
  awk -F'\t' 'NR>1 && $9=="OK" {print $1"\t"$2"\t"$3"\t"$4"\t"$5}' "$MAN" | while IFS=$'\t' read -r kind run path bytes files; do
    [ -e "$path" ] || continue
    if [ "$kind" = tbt ]; then
      [ "$(stat -c %s "$path")" = "$bytes" ] || { log "SKIP delete tbt $run: size changed"; continue; }
      rm -f "$path"
    else
      read -r n b < <(find "$path" -type f -printf '%s\n' | awk '{n++; s+=$1} END{print n+0, s+0}')
      [ "$n" = "$files" ] && [ "$b" = "$bytes" ] || { log "SKIP delete alog $run: contents changed"; continue; }
      rm -rf "$path"
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$kind" "$run" "$path" "$bytes" "$(date '+%F %T')" >> "$DEL"
  done
  log "=== delete done: $(($(wc -l < "$DEL") - 1)) removed"
  ;;
*) echo "usage: $0 archive|delete" >&2; exit 2 ;;
esac
