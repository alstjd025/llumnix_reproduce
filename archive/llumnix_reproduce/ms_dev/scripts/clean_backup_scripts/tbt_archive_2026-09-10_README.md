# tbt_events.jsonl archive — 2026-09-10

## 이것이 무엇인가

`Agent_applications/agent_motivation_experiment/results/<run>/tbt_events.jsonl` 파일
140개를 루트 디스크에서 이 디렉토리로 옮긴 것이다. 각 파일은 원래 run 디렉토리와 같은
이름의 하위 디렉토리 아래에 원래 파일명 그대로 들어 있다.

    <this dir>/<run_dir_name>/tbt_events.jsonl

`tbt_events.jsonl`은 토큰 하나하나의 타이밍 이벤트이고 결과 용량의 대부분을 차지한다.
run 디렉토리의 나머지(`metrics.csv`, `server_metrics/`, `run_config.json`,
`request_ids.jsonl`, `agent_logs/`, `shards/`)는 **하나도 건드리지 않았다** —
전부 원래 자리에 그대로 있다. 즉 SLO 달성률·거절률·goodput 같은 요청 단위 지표와
엔진 지표는 루트 디스크에서 예전과 똑같이 읽힌다. 이 파일들을 읽는 것은
ITL CDF 그림 둘과 요청 안 p99뿐이다.

## 왜 옮겼는가

2026-09-10 새벽에 루트 파일시스템이 1.6T 중 304G만 남아(82% 사용) 있었다. 여덟
인스턴스 한 시간 run 하나가 28~32 GB이고 세 정책을 비교하면 90~105 GB가 필요한데,
노드가 90%를 넘으면 kubelet이 DiskPressure로 파드를 축출해서 제어평면과 돌고 있는
실험이 같이 죽는다. 그래서 밤에 걸 실험이 돌 수 있도록 공간을 만들었다.

사용자가 이 정리를 명시적으로 승인했다 (2026-09-10):
"용량 부족하면 tbt 저건 조금씩 지우면서 해도 돼. 혹은 /NHNHOME 디렉토리에 전에 백업을
좀 했었는데, 그 경로로 옮기고 확인하고 지워도 되고. 마음대로 해. 다만 중요 실험
데이터나 코드가 아예 유실되지않게 주의하고"

결과: 608 GiB를 비웠고 루트 여유가 304G에서 912G(44% 사용)가 됐다.

## 왜 이 경로인가

원래 목표 경로는 `/NHNHOME/NXC_ROOT/NXC13/tbt_archive_2026-09-10/`이었는데,
`/NHNHOME/NXC_ROOT/NXC13`은 소유자가 `iitp_inse`이고 모드가 `drwxr-sr-x`라서 그룹에
쓰기 권한이 없다. 계정 `nxclab`으로는 그 아래에 디렉토리를 만들 수 없다. 그래서 같은
NXC13 안에서 `nxclab` 소유이고 이미 이 기계의 백업이 들어 있는
`home_backup_2026-08-28/` 아래에 만들었다.

### 이 경로가 어느 디스크인가 (중요)

`/NHNHOME` 자체는 로컬 XFS 디스크(`/dev/nvme1n1p1`, 3.2T)인데 **`/NHNHOME/NXC_ROOT`는
그 위에 따로 마운트된 Lustre 병렬 파일시스템이다**(59T 중 42T 여유). 따라서 이 아카이브는
로컬 NVMe가 아니라 공유 스토리지에 있다. `df -h /NHNHOME`으로 보면 사용량이 안 늘어난
것처럼 보이므로 확인할 때는 `df -h /NHNHOME/NXC_ROOT`를 본다. 읽기 속도는 로컬 디스크보다
느리므로, 이 파일들을 다시 분석에 쓸 때는 필요한 것만 루트로 되돌리는 편이 낫다.

## 무엇을 옮기지 않았는가 (보호 대상)

- **논문 manifest가 참조하는 run 138개 (79.0 GiB)** — `paper_experiment/*/manifest.tsv`
  네 파일(`eval_2026-08-21`, `qoserve_engine_2026-08`, `static_sweep_2026-08`,
  `static_sweep_clean_2026-08`)에 적힌 run 이름 198개를 모두 읽어 보호 집합으로 썼다.
- **2026-09-08 이후 run 32개 (195.8 GiB)** — 디렉토리 이름이 `260908` 이상인 것.
  지금 분석 중인 여덟 인스턴스 실험이다.
- **2026-09-07 run 14개 (약 79 GiB)** — 목표치인 여유 900 GiB에 먼저 도달해서 멈췄다.
  오래된 것부터 옮겼기 때문에 남은 것이 가장 최근 것이다.

## 어떻게 검증했는가

파일 하나마다 목적지에 복사한 뒤 **바이트 수와 줄 수를 원본과 목적지 양쪽에서 재서
둘 다 일치할 때만** 원본을 지웠다. 하나라도 어긋나면 원본을 그대로 두고 기록하게 되어
있었는데, 실패한 파일은 **0개**다. 복사 도중 원본 크기가 변하지 않았는지도 확인했다.

## 되돌리는 방법

원래 자리로 그대로 복사하면 된다.

    cp /NHNHOME/NXC_ROOT/NXC13/home_backup_2026-08-28/tbt_archive_2026-09-10/<run>/tbt_events.jsonl \
       /home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment/results/<run>/tbt_events.jsonl

전부 되돌리려면 (루트에 609 GiB가 필요하다):

    A=/NHNHOME/NXC_ROOT/NXC13/home_backup_2026-08-28/tbt_archive_2026-09-10
    tail -n +2 $A/MANIFEST.tsv | while IFS=$'\t' read -r orig arch bytes lines; do
      mkdir -p "$(dirname "$orig")" && cp "$arch" "$orig"
    done

`MANIFEST.tsv`에 원본 경로 / 보관 경로 / 바이트 수 / 줄 수가 파일마다 한 줄씩 있으므로,
되돌린 뒤 `wc -c`와 `wc -l`로 그 값과 대조하면 검증이 된다.

## 옮긴 양 (run 날짜별, 디렉토리 이름의 YYMMDD는 UTC-7이다)

  260827    3 files     6.8 GiB
  260831   10 files    20.0 GiB
  260901    9 files    31.8 GiB
  260902    1 files     0.0 GiB
  260904   46 files   156.6 GiB
  260905   38 files   165.5 GiB
  260906   32 files   199.4 GiB
  260907    1 files    28.4 GiB

합계 140개 파일, 608 GiB.

## 병합이 안 된 run

`results/` 전체에서 `shards/` 하위 디렉토리가 남아 있는 run은 하나다.

    260902_0055_exp110r4_llmdslot75_t75fair_rpm_600

`shards/`가 남아 있다는 것은 워커별 shard의 병합이 끝나기 전에 러너가 죽었다는 신호라서
보통은 데이터가 shard에만 있다는 뜻인데, **이 run은 shards/가 비어 있고**
`metrics.csv`도 608바이트(헤더 수준)이며 `errors.log`가 있다. 부하를 만들기 전에 죽은
run으로 보인다. 이 디렉토리는 건드리지 않았다(`shards/`도 그대로다). 복구가 필요하면
`analysis_scripts/recover_unmerged_shards.py`가 있지만 이번에는 돌리지 않았다.
