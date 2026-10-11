# AC 설계 노트 — 예측/가설/모델 상세

## 가설 (실험 전 예상)

H1. **P1(동시성 상한)만으로도 goodput의 대부분을 회수**할 수 있다.
   근거: 실측에서 TTFT 폭발은 순수 큐잉(λ>μ)이고, TBT는 배치 크기의 함수.
   in-flight cap N을 "fleet가 TBT 50ms를 지키는 총 동시성"으로 잡으면 둘 다
   간접 통제됨. 예상 N: 60 req/s 조건에서 관측된 정상 동시성 ~2000-2200 부근
   이 상한; TBT 여유를 보려면 그보다 낮게 (ITL 실측: 60req/s에서 meanTBT
   46ms로 이미 바 근처 → N* 은 1200-1800 사이로 예상).

H2. **P4(KV 기준)는 이 워크로드에선 둔감**할 것. KV 100%는 이미 과부하가
   한참 진행된 후에 도달(80 req/s에서도 fill-up에 ~50s) — KV는 후행 지표.
   단, long-output 워크로드에선 유효할 것.

H3. **P5(TTFT 예측)가 fail-fast 품질이 가장 좋다** — 대기 큐 길이는 선행
   지표라 큐가 5s어치를 넘는 순간부터 즉시 reject 가능. 예측 오차는
   prefill 처리율 추정(실측 ~90-110k tok/s fleet)의 ±20% 수준이면 충분.

H4. P7(P5∧P6)이 최종 우승, 그러나 P1 대비 이득은 크지 않을 것(단순함 대비).
   → "가장 단순하면서 충분한" 정책을 찾는 것이 목표.

H5. reject된 요청 비율 = max(0, 1-μ_slo/λ) 근처가 이론 하한; 이보다 크게
   reject하면 과보수(goodput 손실), 작으면 admit된 것들이 SLO 미스.

## 시뮬레이터 모델 (Stage A)

이벤트 드리븐, 1 tick = 100ms (또는 이벤트 기반 정밀).

**엔진 모델** (4개, 실측 캘리브레이션):
- KV pool: 585,120 tok/engine (36570 blocks × 16). request 점유 = input+생성누적.
- max_num_seqs = 1024.
- decode: step time τ(B) — 실측 ITL vs per-engine batch 곡선에서 적합
  (구간: B~50→ITL~40ms? 실측 포인트: 60req/s B~450/engine → meanTBT 46ms;
  100req/s B~700 → ITL 100-150ms. EXP-05의 30/50/70/90 포인트로 보간 개선).
  스텝마다 running 전원이 1 토큰씩 진행.
- prefill: fleet prefill 처리율 P ≈ 실측 90-110k tok/s (EXP-04 D패널),
  running 배치에 편입 전 대기 큐에서 순차 소진. chunked-prefill 단순화:
  prefill과 decode가 토큰 예산 공유(max_num_batched_tokens 8192/step).
- KV 부족 시: admit 중단(waiting에 적체) — vLLM처럼 preemption까지는 모사
  안 함(그 영역에 들어가면 이미 SLO 실패라 판정에 영향 없음).

**dispatch**: 4엔진 라운드로빈(균형 가정; herd는 warmup으로 제거된 EXP-05
데이터 기준이라 타당).

**요청**: EXP-05 metrics.csv의 (input_tokens, output_tokens) 튜플을 재현
셔플로 샘플. 도착 = 고정 rate (실험과 동일).

**SLO 판정**: sim-TTFT = admit→첫 decode 스텝 완료, sim-TBT = 생성 구간
평균 스텝시간. 실측과 동일 기준 (5s / 50ms).

**정책 인터페이스**:
```python
class Policy:
    def on_arrival(self, req, state) -> bool  # admit?
    # state: per-engine {running, waiting_tokens, kv_used}, global admitted rate
```

**캘리브레이션 검증**: P0(no-AC) 시뮬을 EXP-05 실측과 비교 — attain%/tok-s
곡선이 정성적으로 일치해야 정책 비교에 사용.

## 라이브 plug-in 스케치 (Stage B)

```
run_experiment.py
  └─ submit 경로: if policy and not policy.admit(live_state): record reject; skip
       live_state ← AdmissionState (신규 모듈 admission_control/policies.py)
         ├─ llumnix_metrics collector 최신 tick 공유 (1s 주기, 이미 존재)
         └─ 자체 카운터 (in-flight, 최근 admit rate)
```
- reject 기록: 기존 스키마 `is_rejected=True, rejection_reason=AC_<policy>`.
- 정책/파라미터는 `--admission-policy`, `--admission-param` 플래그.
- MP 모드: 워커별 로컬 state(1/N 스케일) — 근사지만 router 현실과 유사.

## 참고 (차용원)

- Llumnix: dispatch threshold(all_prefills 8192)와 max-queue-size(512)가
  사실상의 유일한 억제 장치인데, 초과 시 reject가 아니라 **sleep-재시도**라
  admission control이 아님 (관측: 503은 15건뿐, 무한대기).
- vLLM(프론트엔드/router): max_num_seqs는 엔진 배치 상한일 뿐 큐는 무한 —
  router 레벨 concurrency cap이 통상적 해법.
- NVIDIA Dynamo router: queue-depth/KV 기반 라우팅+backpressure 아이디어 차용
  (P2/P4).
- Halo(자체 프로젝트): request-level SLO 예측 admission — P7과 동형, 비교
  기준으로 유용.
