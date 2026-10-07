# 실험 결과의 백업·이동·삭제 기록 (정본)

**이 디렉토리가 백업·정리·복원에 관한 유일한 기록 장소다 (2026-10-05 사용자 지시).**
스크립트, 삭제 목록, 매니페스트, 복원 절차 문서를 전부 여기에 두고, **무엇이 어디로 옮겨지고
무엇이 지워졌는지는 아래 §2에 날짜순으로 한 줄씩 추가한다.** 다른 문서(STATUS.md, 실험 기록)에는
이 디렉토리를 가리키는 링크만 둔다. 기록이 여러 곳에 흩어지면 무엇이 남아 있는지 추적할 수 없다.

## 1. 지금 데이터가 어디에 있나 (2026-10-07 기준)

| 위치 | 무엇 | 기록 |
|---|---|---|
| 노드 `.../agent_motivation_experiment/results/` | **2026-10-06에 컨테이너를 초기화해서 run 디렉토리가 하나도 없다**(`aggregate_analysis/`만 git에서 왔다). 초기화 전에는 `metrics.csv`·`server_metrics/`가 있는 run 1,841개, 123 GB였다. 1,841개 전부 아래 Lustre `results_priority/`(1,296개)나 맥 SSD(1,841개 전부)에 사본이 있다 — §2의 2026-10-06 항목 | |
| Lustre `/NHNHOME/storage/home_backup_2026-08-28/` (2026-10-05까지는 `/NHNHOME/NXC_ROOT/NXC13/home_backup_2026-08-28/`로 보였다) | `results_priority/`(08-28 백업, 151 GB), `tbt_archive_2026-09-10/`(tbt 140개, 608 GiB, 비압축), `results_archive_2026-10-05/`(tbt·agent_logs, zstd 압축) | 각 하위 디렉토리의 README/MANIFEST, 그리고 그 사본이 이 디렉토리에 있다 |
| 맥에 연결한 외장 SSD `/Volumes/volume1/nxc13_backup_2026-09-23/` | tbt와 agent_logs를 뺀 results(122 GB), git이 추적하지 않는 파일, agent_logs tar.gz | `RESTORE-2026-09-23.md`, `backup_manifest_node_2026-09-23.tsv` |
| GitHub | 두 저장소 | |

**Lustre는 공유 파일시스템이다**(59 TB, 2026-10-07에 33 TB 여유). 우리 디렉토리의 부모(지금
`/NHNHOME/storage`, 예전 `/NHNHOME/NXC_ROOT/NXC13`)는 `iitp_inse` 소유라 `nxclab`이 쓸 수 없고, 우리 것은
전부 `nxclab` 소유인 `home_backup_2026-08-28/` 아래에 있다. 용량은 `df -h /NHNHOME`(로컬 NVMe)이 아니라
`df -h /NHNHOME/storage`로 본다.

⚠ **마운트 경로는 컨테이너가 만들어질 때마다 바뀔 수 있다.** 2026-10-06에 컨테이너를 새로 만든 뒤
`/NHNHOME/NXC_ROOT`는 마운트되지 않았고, 같은 디렉토리가 `/NHNHOME/storage/` 아래에 보인다. 같은
디렉토리라는 것은 2026-10-07에 확인했다 — `results_archive_2026-10-05/`의 tbt 345개·agent_logs 1,841개가
그대로 있고, `DELETED.tsv`가 이 디렉토리의 사본과 바이트 단위로 같다. 이 디렉토리의 옛 문서(RESTORE-*.md
등)에 적힌 `/NHNHOME/NXC_ROOT/NXC13/...`는 `/NHNHOME/storage/...`로 바꿔 읽는다.

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
- delete 단계 (2026-10-05 18:19:37 ~ 18:34:29 KST, **사용자가 직접 실행**. Claude Code 권한 검사가
  되돌릴 수 없는 삭제로 막았기 때문이다): **2,186개 삭제, SKIP 0.**
  - tbt_events 345개 838.7 GB, agent_logs 1,841개 run 217.0 GB.
  - MANIFEST의 OK 항목과 `DELETED.tsv`가 정확히 일치한다(빠진 것 0, 목록 밖 삭제 0).
  - 노드에 남은 tbt_events 0개, agent_logs 0개. `metrics.csv`와 `server_metrics/`는 1,841개 run 전부 남아 있다.
  - `results/` 실제 사용량(`du`): 약 1.19 TB → **123 GB.**
  - 삭제 후 Lustre 압축본 표본 검사(`zstd -t`, 종류마다 3개) 정상.
- ⚠ **삭제했는데 디스크 공간이 돌아오지 않았다.** `df -h /`가 삭제 전후로 거의 같았다(사용 771 → 772 GB).
  처음에는 이 셸의 `df`가 다른 디스크를 재고 있다고 적었는데 **틀렸다.** 원인은 지운 파일이 컨테이너
  이미지의 읽기 전용 layer 안에 있었던 것이다. 근거는 GPU 클러스터 플랫폼이 남긴 컨테이너 로그
  `/NHNHOME/NXC_ROOT/.gc/NXC13_*_log.out`(2026-10-05에 읽음. 2026-10-07 지금 컨테이너에서는 그 경로가
  보이지 않는다)이다.
  1. 2026-10-03 15:01에 08-31부터 돌던 컨테이너가 종료되면서 플랫폼이 컨테이너 전체를
     `nvidia-docker commit NXC13 .../nxclab-nxc13:2`로 이미지로 만들고 registry에 push했다. 이미지가 `:1`의
     26.1 GB에서 `:2`의 **1.29 TB**가 됐다 — 그때 노드에 있던 results 1.19 TB가 전부 이미지에 들어갔다.
     commit과 push에 2시간 16분이 걸렸다(15:01 → 17:17).
  2. 2026-10-04 12:03과 20:57에 플랫폼이 그 `:2` 이미지로 컨테이너를 새로 만들었다. 생성 옵션의
     `--storage-opt size=1600G` 때문에 `df`의 `/` 크기가 1.6T로 보인다.
  3. 그래서 10-04 이전의 모든 파일은 overlay 파일시스템의 lower layer(읽기 전용)에 있었다. lower layer의
     파일을 지우면 쓰기 가능한 upper layer에 "이 파일은 지워졌다"는 표시(whiteout)만 생기고 데이터는 lower
     layer에 그대로 남는다. `rm`은 성공하고 `du`도 123 GB로 줄지만 `df`는 그대로인 이유가 이것이다.
  - **평범한 `docker commit`을 다시 해도 공간은 줄지 않는다.** commit은 기존 layer를 그대로 두고 upper layer를
    새 layer로 하나 더 얹으므로, 새 이미지는 기존 1.29 TB에 whiteout이 담긴 layer가 더해진 크기가 된다.
    공간을 돌려받으려면 layer를 하나로 합친 이미지를 만들거나(`docker export | docker import`, 실행 설정
    ENV·CMD는 옮겨지지 않는다) 작은 기본 이미지 `:1`에서 컨테이너를 새로 만들어야 하고, 둘 다 플랫폼 쪽
    작업이다. registry에 남은 `:2`(1.29 TB)는 관리자가 지우지 않으면 registry 공간을 계속 차지한다.
  - 이 항목은 2026-10-05에 커밋 `5a30e1a`로 적었는데 **push하기 전에 컨테이너가 초기화되어 사라졌고**,
    2026-10-07에 대화 기록에서 다시 적었다.

### 2026-10-06 — 사용자가 컨테이너를 초기화, 저장소를 GitHub에서 다시 클론
- 위 정리와 push가 끝난 뒤 사용자가 컨테이너를 초기화했다. 새 컨테이너의 `/`는 1.6 TB 중 588 MB 사용으로
  시작했다(위 overlay 문제는 이것으로 해소됐다). 두 저장소는 GitHub에서 다시 클론했다.
- **잃은 것**
  - push하지 않은 커밋 `5a30e1a` 하나(위 항목, 다시 적었다).
  - 노드의 `results/` run 1,841개(123 GB). **사본이 없는 run은 0개다** — `DELETED.tsv`의 run 목록 1,841개를
    Lustre `results_priority/results/`(그 안에 `metrics.csv`가 있는 run 1,296개)와 맥 SSD 매니페스트
    `backup_manifest_node_2026-09-23.tsv`에 대조했다. 마지막 run이 09-17(`260917_1843_exp139…`)이라 SSD
    백업(09-23) 이후에 생긴 run은 없다. ⚠ **545개 run은 맥 SSD에만 있다.** 다시 분석하려면 SSD에서 옮겨야 한다.
  - 배포돼 있던 `bin/scheduler-exp07`(STATUS.md의 `9e8f68b6`). `bin/`은 git 밖이고 아래 tgz에도 없다.
    `tools/staging/scheduler-usec`가 같은 파일일 가능성이 높으나 md5를 대조하기 전까지는 확정이 아니다.
  - `/home/nxclab/tools/`, Go 툴체인, k3s 클러스터, `lib/sglang` 서브모듈 체크아웃.
- **초기화 직전에 사용자가 만든 백업** `/NHNHOME/share/nxclab_backup_1006/`(Lustre, 2026-10-06 19:06):
  `home_misc_1006.tgz`(2.9 GB, 항목 17,807개 — `tools/` 16,790개, `scratch/` 948개, `handover/` 50개,
  `bk1006/env/` 16개), `powertest_1006.tgz`(다른 프로젝트), `env/`(환경 기록), `MD5SUMS`.
  `tools/`에는 Go 툴체인, `bin-backup/` 66개(`scheduler-exp07.pre-exp140` = `063e1c85` 포함), 실험 체인
  스크립트 236개(`exp139b.sh`·`exp140*.sh` 포함), `staging/`이 들어 있다. 저장소 디렉토리 자체는 이 tgz에 없다.
- 클론 직후 체크아웃된 기본 브랜치는 `feat/fluidserve`(08-06)이고, 최신 작업은 `feat/gate-cost-instrumentation`
  이다. 브랜치를 바꿔야 한다.

## 3. 압축 아카이브에서 꺼내는 법

    L=/NHNHOME/storage/home_backup_2026-08-28/results_archive_2026-10-05   # 2026-10-05까지는 /NHNHOME/NXC_ROOT/NXC13/...
    R=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment/results
    # tbt 하나
    zstd -dc $L/tbt_events/<run>.tbt_events.jsonl.zst > $R/<run>/tbt_events.jsonl
    # 읽기만 할 거면 풀지 않고 스트림으로: zstd -dc ... | python3 script.py -
    # agent_logs 하나
    zstd -dc $L/agent_logs/<run>.agent_logs.tar.zst | tar -C $R/<run> -xf -
