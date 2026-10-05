# 실험 결과의 백업·이동·삭제 기록 (정본)

**이 디렉토리가 백업·정리·복원에 관한 유일한 기록 장소다 (2026-10-05 사용자 지시).**
스크립트, 삭제 목록, 매니페스트, 복원 절차 문서를 전부 여기에 두고, **무엇이 어디로 옮겨지고
무엇이 지워졌는지는 아래 §2에 날짜순으로 한 줄씩 추가한다.** 다른 문서(STATUS.md, 실험 기록)에는
이 디렉토리를 가리키는 링크만 둔다. 기록이 여러 곳에 흩어지면 무엇이 남아 있는지 추적할 수 없다.

## 1. 지금 데이터가 어디에 있나 (2026-10-05 기준)

| 위치 | 무엇 | 기록 |
|---|---|---|
| 노드 `.../agent_motivation_experiment/results/` | run 디렉토리 1,843개. `metrics.csv`, `server_metrics/`, `request_ids.jsonl`, `run_config.json` 등. **`tbt_events.jsonl`과 `agent_logs/`는 2026-10-05 이동 후 노드에 없다**(§2의 2026-10-05 항목이 끝났는지 확인할 것) | |
| Lustre `/NHNHOME/NXC_ROOT/NXC13/home_backup_2026-08-28/` | `results_priority/`(08-28 백업, 151 GB), `tbt_archive_2026-09-10/`(tbt 140개, 608 GiB, 비압축), `results_archive_2026-10-05/`(tbt·agent_logs, zstd 압축) | 각 하위 디렉토리의 README/MANIFEST, 그리고 그 사본이 이 디렉토리에 있다 |
| 맥에 연결한 외장 SSD `/Volumes/volume1/nxc13_backup_2026-09-23/` | tbt와 agent_logs를 뺀 results(122 GB), git이 추적하지 않는 파일, agent_logs tar.gz | `RESTORE-2026-09-23.md`, `backup_manifest_node_2026-09-23.tsv` |
| GitHub | 두 저장소 | |

**Lustre는 공유 파일시스템이다**(59 TB, 2026-10-05에 33 TB 여유). `/NHNHOME/NXC_ROOT/NXC13` 자체는
`iitp_inse` 소유라 `nxclab`이 쓸 수 없고, 우리 것은 전부 `nxclab` 소유인 `home_backup_2026-08-28/`
아래에 있다. 용량은 `df -h /NHNHOME`이 아니라 `df -h /NHNHOME/NXC_ROOT`로 본다.

⚠ **`tbt_events.jsonl`을 읽는 것은 "ITL CDF 그림 둘"만이 아니다.** 2026-09-01부터 논문의 주 지표인
누적 마감 채점(`analysis_scripts/request_level/deadline_ladder_attainment.py`, 규칙 `ladder95`)이 이
파일을 읽는다. 아래의 09-10·09-11 기록과 `tbt_archive_2026-09-10_README.md`, `RESTORE-2026-09-23.md`
§1에 적힌 "ITL CDF만 읽는다"는 그 시점의 서술이고 지금은 틀렸다. 논문 run의 채점 결과는
`results/aggregate_analysis/ladder95/`와 `paper_figures/*.csv`에 캐시되어 있으므로 그림을 다시 그리는
데는 필요 없고, **다른 규칙이나 다른 SLO 배수로 다시 채점할 때 필요하다.**

## 2. 날짜순 기록

각 항목: 무엇을 / 얼마나 / 어디서 어디로 / 누가 승인했나 / 근거 파일.

### 2026-08-05 — 병합이 끝난 shard 삭제
- 러너가 워커별 shard를 병합한 뒤 지우지 않아 토큰 타이밍 이벤트가 두 벌 있었다. 사용자 지시로
  shard를 전부 지웠다. `results/` 1.5 TB → 777 GB. 지우기 전에 792개 run 전부 병합을 대조했고 병합본이
  없던 5개는 복구했다. 이후 `_merge_load_shards`가 검증 후 shard를 지운다. (CLAUDE.md 함정 B, 목록 파일 없음)

### 2026-08-26 — 삭제 제안 스크립트 작성, 실행하지 않음
- `free_disk_PROPOSAL.sh`: 논문 manifest가 참조하지 않고 N일보다 오래된 run의 tbt만 지우는 스크립트.
  **실행되지 않았다.**

### 2026-08-27 — swe transcript 유실과 복구
- swe 클래스의 입력 transcript 세 파일이 `results/exp10_transcript/`에 있다가 결과 정리 중 같이 지워졌다.
  재생성하고 요청 단위로 대조해 복구했고, 지금은 `workloads/codingagent_request_level_poisson/data/`에 있다.
  (CLAUDE.md 함정 B "워크로드의 입력은 결과가 아니다")

### 2026-08-28 — Lustre로 백업 (`home_backup_2026-08-28/`)
- `results_priority/` 151 GB(파일 558만), 저장소 사본, tools, dotfiles, cluster_state.
- 절차 `RESTORE-2026-08-28.md`, 사후 매니페스트 `BACKUP_MANIFEST-2026-08-28.md`(사후에 만든 것이라
  "빠진 것이 없다"는 말할 수 없다는 한계가 그 안에 적혀 있다).

### 2026-08-31 — Lustre 백업에서 노드로 복원
- `restore_results.sh`로 `results_priority/`를 노드에 되돌렸다. 로그 `restore_results.log`,
  원본 크기 `src_results_size.txt`(414,660,228,099 바이트), `cp_orig.log`.

### 2026-09-10 — tbt_events 114개 삭제 (160 GB)
- 이름이 저장소 어느 문서에도 없고 날짜가 2026-09-04 이전인 run의 tbt. 여유 145 → 304 GB.
  사용자 승인. 목록 `deleted_tbt_2026-09-10.txt`(원래 `Agent_applications/.../housekeeping/`에 있었다).
  **사본 없음 — 되돌릴 수 없다.**

### 2026-09-10 — tbt_events 140개를 Lustre로 이동 (608 GiB, 비압축)
- 노드 → `home_backup_2026-08-28/tbt_archive_2026-09-10/<run>/tbt_events.jsonl`. 파일마다 바이트 수와
  줄 수를 양쪽에서 대조한 뒤 원본을 지웠다. 여유 304 → 912 GB. 사용자 승인
  ("/NHNHOME 디렉토리에 ... 그 경로로 옮기고 확인하고 지워도 되고").
  `wait_for_disk_quiet.sh`가 목표 여유에 도달한 것을 확인했다(`wait_for_disk_quiet.log`).
- 아카이브 쪽 README와 MANIFEST 사본: `tbt_archive_2026-09-10_README.md`, `tbt_archive_2026-09-10_MANIFEST.tsv`.

### 2026-09-11 — tbt_events 67개 삭제 (359.9 GB)
- EXP-114(인프라 디버깅 아크, 정책 수치 인용 금지)와 EXP-118/119/120(기각된 수정 셋). 여유 167 → 527 GB.
  사용자 지시. 목록 `exp_tbt_delete_2026-09-11.tsv`(원래 `/home/nxclab/tools/`에 있었다).
  **사본 없음 — 되돌릴 수 없다.**

### 2026-09-23 — 맥 외장 SSD로 백업
- tbt와 agent_logs를 뺀 results 122 GB, git 미추적 파일 3.9 GB, agent_logs run별 tar.gz 1,841개.
  전송 전에 노드에서 정답표를 만들고 양방향으로 대조했다. `RESTORE-2026-09-23.md`,
  `backup_manifest_node_2026-09-23.tsv`. **tbt_events는 의도적으로 뺐다.**

### 2026-10-05 — 기록을 이 디렉토리로 모음
사용자 지시: "ms_dev/scripts/clean_backup_scripts 로 하고 ... 한곳에 날짜로 기록해야 tracking이 정확하니까".

| 파일 | 원래 위치 |
|---|---|
| `RESTORE-2026-08-28.md`, `RESTORE-2026-09-23.md`, `BACKUP_MANIFEST-2026-08-28.md`, `backup_manifest_node_2026-09-23.tsv` | `ms_dev/notes/` (`git mv`) |
| `deleted_tbt_2026-09-10.txt` | `Agent_applications/agent_motivation_experiment/housekeeping/` (그 저장소에서 `git rm`, 빈 디렉토리 제거) |
| `exp_tbt_delete_2026-09-11.tsv`, `free_disk_PROPOSAL.sh`, `restore_results.sh`, `restore_results.log`, `cp_orig.log`, `src_results_size.txt`, `wait_for_disk_quiet.sh`, `wait_for_disk_quiet.log` | `/home/nxclab/tools/` (`mv`) |
| `tbt_archive_2026-09-10_README.md`, `tbt_archive_2026-09-10_MANIFEST.tsv` | Lustre 아카이브에서 **복사**(원본은 아카이브 옆에 그대로 둔다) |

경로를 고친 문서: `ms_dev/notes/STATUS.md`(512행, 1233행), `RESTORE-2026-09-23.md`(32행, 90행),
`Agent_applications/.../experiments/EXP-114_llama8b-8instances.md`(4790행).

**옮기지 않은 것**: `/home/nxclab/tools/`의 실험 체인 스크립트와 그 로그·pid(약 610개)는 백업·정리가
아니라 실험이 어떻게 걸렸는지의 기록이라 이번 범위에서 뺐다. `bin-backup/`(배포 바이너리, CLAUDE.md가
경로를 참조)과 `staging/`도 그대로다.

### 2026-10-05 — tbt_events와 agent_logs를 압축해서 Lustre로 이동
- 사용자 지시: "압축해서 옮기는게 좋을것같아 ... 압축해서 옮긴것도 잘 기록해두고".
- 스크립트 `archive_to_lustre_2026-10-05.sh`. 목적지 `home_backup_2026-08-28/results_archive_2026-10-05/`.
  - `tbt_events/<run>.tbt_events.jsonl.zst` — zstd 레벨 9. 원본의 sha256과, Lustre에서 다시 읽어 압축을
    푼 것의 sha256이 같아야 통과.
  - `agent_logs/<run>.agent_logs.tar.zst` — run마다 tar 하나. Lustre에서 다시 읽어 `tar --diff`로 원본
    디렉토리와 파일마다 내용·크기·수정 시각을 비교하고, 파일 수와 총 바이트도 대조한다.
  - 검증 전에는 `.part` 이름으로 쓰고, 통과한 뒤에 이름을 바꾼다.
- 두 단계: `archive`(복사·검증·`MANIFEST.tsv`, 아무것도 지우지 않음) → `delete`(MANIFEST가 OK로 적은
  항목만, 원본이 그 사이 안 바뀌었는지 다시 확인하고 지움, `DELETED.tsv`).
- 사본 `results_archive_2026-10-05/{MANIFEST.tsv, DELETED.tsv, archive.log}`가 이 디렉토리에 생긴다.
- 사전 측정(1 GB 표본): 압축률 12.2배, 2.0 GB/s, Lustre 왕복 검증 sha256 일치.
- 결과 (archive 단계, 2026-10-05 17:58:37 ~ 18:07:58 KST, 약 9분 반): **2,186개 전부 OK, FAIL 0.**
  - tbt_events 345개: 838.7 GB → **96.7 GB** (8.7배)
  - agent_logs 1,841개 run, 파일 55,248,554개: 217.0 GB → **15.9 GB** (13.6배)
  - `.part` 0개, MANIFEST 중복 0개, 목적지 파일 수 345 / 1,841로 일치.
- delete 단계: **아직 실행하지 않았다.** 노드의 원본은 그대로 있다(2026-10-05 18:10 기준).
  실행하면 `DELETED.tsv`가 생기고 이 줄을 고친다.

## 3. 압축 아카이브에서 꺼내는 법

    L=/NHNHOME/NXC_ROOT/NXC13/home_backup_2026-08-28/results_archive_2026-10-05
    R=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment/results
    # tbt 하나
    zstd -dc $L/tbt_events/<run>.tbt_events.jsonl.zst > $R/<run>/tbt_events.jsonl
    # 읽기만 할 거면 풀지 않고 스트림으로: zstd -dc ... | python3 script.py -
    # agent_logs 하나
    zstd -dc $L/agent_logs/<run>.agent_logs.tar.zst | tar -C $R/<run> -xf -
