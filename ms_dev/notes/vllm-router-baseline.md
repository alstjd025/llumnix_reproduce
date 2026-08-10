# vLLM production stack router를 기준선으로 세우는 계획 (2026-08-10)

**상태**: 계획만 있고 아무것도 안 돌렸다. `llmd-baseline.md`와 같은 형식이다 — 답하려는 질문,
무엇을 이식하는가, 실행 전에 적어 두는 판정 규칙, 그리고 **이 기준선이 답하지 않는 것**.

⚠ **실행 전에 §5의 사전 판정 규칙을 읽는다. 결과에 맞춰 고치지 않는다.**

## 0. 이 문서가 생긴 이유 — 그리고 앞서 한 권고를 철회한다

`motivation_v3.md` §5.2가 **"vLLM production stack router 이식은 안 하는 편이 낫다"**고 적었고,
근거는 **"분류 표에서 Llumnix와 같은 칸(A0×B2)이라 새 축을 만들지 않는다"**였다. **철회한다.**
두 가지를 놓쳤다.

1. **수정 후 워크로드에서 A0×B2 칸이 지금 비어 있다.** Llumnix 부하 균등화의 마지막 측정은
   2026-08-06의 EXP-61이고 **2026-08-08의 부하 생성기 수정 이후로는 한 번도 안 돌았다.** 즉
   "거절도 결합도 없는" 극단이 지금 논문의 표에 없다.
2. **그 칸 안에서 두 시스템이 같지 않다.** Llumnix는 **인스턴스별 대기 prefill 토큰 수가 가장
   적은 곳**으로 보내므로 부하를 본다. 아래에서 확인했듯 production stack의 round-robin은
   **부하를 아예 안 본다** — 엔진 통계를 인자로 받아 놓고 쓰지 않는다. **우리 두 축이 그 차이를
   담지 못하는 것이지 차이가 없는 것이 아니다.**

**그리고 심사 관점에서 이쪽이 더 값이 크다.** "분류 표의 같은 칸"은 우리가 만든 좌표계이고,
심사위원이 실제로 묻는 것은 **"사람들이 실제로 배포하는 라우터보다 나은가"**다. Llumnix는 논문
시스템이고 이것은 vLLM 프로젝트가 배포하는 스택이다.

## 1. ⚠ 먼저: "vLLM router"라는 이름의 서로 다른 두 물건이 있다

**이것을 확인하지 않고 이식하면 엉뚱한 것을 이식한다.** 2026-08-10에 둘 다 소스를 받아
확인했다.

| | **vllm-project/production-stack** | **PyPI `vllm-router` 0.1.15** |
|---|---|---|
| 무엇 | vLLM 프로젝트가 배포하는 스택의 라우터. **Python** | **Rust** 기반 별도 패키지. 저자가 SGLang 라우터 저자다 |
| 라우팅 선택지 | `roundrobin`, `session`, `kvaware`, `prefixaware`, `disaggregated_prefill`, `disaggregated_prefill_orchestrated` | `random`, `round_robin`, `cache_aware`, `power_of_two`, `consistent_hash` |
| 기본값 | **코드에는 기본값이 없다** — `--routing-logic`을 안 주면 파서가 오류를 낸다. 그런데 **Helm 차트가 `roundrobin`을 기본값으로 배포한다**(`helm/values.yaml`의 `routingLogic: "roundrobin"`) | **`cache_aware`** (`RouterArgs.policy`의 기본값) |
| 우리가 쓸 것 | **이쪽. `roundrobin`.** | 아니다 |

> **이식 대상은 vllm-project/production-stack의 `roundrobin`이고, 논문에는 "vLLM production
> stack router (round-robin, the routing logic its Helm chart ships)"라고 적는다.** 그냥
> "vLLM router"라고 쓰면 위 둘 중 어느 것인지 알 수 없다.

## 2. 그 라우터가 실제로 무엇을 하는가 — 소스에서 확인한 것

`src/vllm_router/routers/routing_logic.py`의 `RoundRobinRouter.route_request`:

```python
def route_request(self, endpoints, engine_stats, request_stats, request) -> str:
    endpoint_urls = self._endpoint_key(endpoints)      # 정렬된 엔드포인트 튜플
    idx = self._next_index.get(endpoint_urls, 0)
    self._next_index[endpoint_urls] = idx + 1
    return endpoint_urls[idx % len(endpoint_urls)]
```

**세 가지가 여기서 곧바로 나온다.**

1. **`engine_stats`와 `request_stats`를 인자로 받아 놓고 한 번도 쓰지 않는다.** 부하를 볼 수
   있는데 안 본다. **Llumnix와의 차이가 이것이다** — Llumnix는 대기 prefill 토큰 수가 가장
   적은 곳을 고른다.
2. **거절이 없다.** 라우팅 함수의 반환형이 엔드포인트 문자열이고 "없음"을 표현할 수 없다.
   과부하는 전부 큐잉과 SLO 위반으로 나타난다. → 분류 표에서 **A0**.
3. **클래스와 인스턴스의 결합이 없다.** 도착 순서대로 돌아가며 배정하므로 모든 클래스가 모든
   인스턴스에 고르게 퍼진다. → 분류 표에서 **B2**. 그리고 이것이 `related-works-review.md`
   §9.2의 **S1 행**("엔진 SLO 스케줄러 + round-robin")이 진단한 구성 그 자체다.

⚠ **`prefixaware`와 `kvaware`도 있다.** 이번에는 안 돌린다 — `kvaware`는 LMCache 컨트롤러를
요구하고 `prefixaware`는 우리 prefix 회계와 축이 겹쳐서 **한 실험에서 두 가지를 바꾸는 것이
된다.** 나중에 따로 세운다(§7).

## 3. 이식이 왜 가벼운가

**우리가 이식하는 것은 알고리즘이지 그 스택이 아니다.** round-robin은 위 여섯 줄이 전부이고,
llm-d 때처럼 별도 배포(Envoy + 예측기 세 파드)를 세울 필요가 없다. 스케줄러에 정책 하나를
추가하고 화이트리스트에 넣으면 된다.

**해야 하는 것 넷.**

1. `pkg/scheduler/policy/`에 `roundrobin` 정책을 추가한다. 인스턴스 목록을 정렬해서 순서대로
   돌린다. **부하도 예산도 보지 않는다** — 그것이 이 기준선의 정의다.
2. `verifySchedulingPolicy` 화이트리스트에 넣는다. **빠지면 기동 시 panic이고 유닛 테스트로는
   안 잡힌다**(CLAUDE.md 함정 A).
3. `set_scheduler_profiling.py`가 그 정책 이름을 받게 한다.
4. 드라이버(`run_exp07.sh` 계열)에 arm을 하나 추가한다.

⚠ **게이트웨이 보유 창은 upstream 기본값 5,000 ms / 재시도 1,000 ms를 준다** — Llumnix SLO,
PolyServe와 같은 처지다. FluidServe 계열의 35,000 ms를 주면 그 라우터에 없는 능력을 주는 것이
된다. ⚠ **그런데 그 5,000 ms가 무해하다는 것은 검증되지 않았다**(`motivation_v3.md` §5.3).

## 4. 실행 전에 반드시 고쳐야 하는 것 — 포트 고갈 (**한쪽은 고쳤다**)

**이 기준선은 거절률이 0%이고, 그것이 EXP-54를 무효로 만든 조건과 정확히 같다.**

EXP-54에서 Llumnix 부하 균등화가 40분에 네 엔진을 포화시키자 오래 큐에 있던 스트림이 끊겼고,
클라이언트가 그 실패를 연결 수준 실패로 인식하지 못해 **비스트리밍 재시도로 연결을 하나 더
열었다.** 임시 포트 약 28,000개가 고갈되어 `[Errno 99] Cannot assign requested address`가
**62,114건** 나왔고, 실패가 즉시 돌아오니 **시도율이 170/s로 읽혔다 — trace의 도착률이 아니라
클라이언트가 헛도는 속도다.** 다른 세 arm에는 이 오류가 0건이다.

**① 재시도 폭주는 2026-08-10에 고쳤다.** `workloads/swe_bench_coding/agent.py`에 분기를
하나 추가해서, `cannot assign requested address` / `max retries exceeded` /
`too many open files` / `address already in use`가 **비스트리밍 재시도로 떨어지지 않게** 했다.
**`is_error`로 표시하고 `is_server_terminated`로는 표시하지 않는다** — 후자는 결과를 알 수 없는
것으로 취급되어 두 분모에서 모두 빠지는데, **클라이언트가 소켓을 다 쓴 것은 측정이 깨진 것이라
보여야 한다.** `is_error`면 offered 분모에 위반으로 남고 `cutoff`에서는 빠진다.

**② 임시 포트 범위는 아직 안 넓혔다.** 러너 파드에 `net.ipv4.ip_local_port_range`를 주려면
`securityContext.sysctls`가 필요하고 그것은 kubelet의 unsafe-sysctl 허용 목록에 걸린다. 지금
`k8s/runner-job.yaml`에는 `securityContext`가 아예 없다. **①만으로 원인이 제거되므로 먼저
①만 넣고 돌리되, 아래 유효성 검사로 확인한다.**

### 4.1 매 조건마다 확인하는 유효성 검사 (실행 전에 정한다)

**이 둘 중 하나라도 걸리면 그 조건은 정책이 아니라 부하 생성기를 잰 것이므로 버린다.**

1. `metrics.csv`의 `error_msg`를 종류별로 세어 **`client connection exhausted`가 0건**이어야
   한다.
2. **"시작된 호출 / 초"가 그 조건의 도착률을 넘지 않아야 한다.** 넘으면 클라이언트가 헛돌고
   있는 것이다.

## 5. 사전 판정 규칙 (실행 전에 쓴다)

**무엇을 돌리는가**: 정적 sweep 8 rate(`10,15,20,25,35,45,55,70` — 다른 네 arm과 같은 격자)
**× 2반복**, 그리고 한 시간 trace **× 2반복**. 정적 16조건 약 3.5시간 + 한 시간 2조건 약
2.3시간 = **약 5.8시간.**

**예상**: 이 라우터는 부하도 예산도 안 보고 거절도 안 하므로 **네 arm 중 가장 낮을 것으로
본다.** 수정 전 워크로드에서 Llumnix 부하 균등화의 90% 유지 도착률이 PolyServe보다 낮았다.

| 결과 | 무엇을 뜻하나 | 문서에 무엇을 하나 |
|---|---|---|
| 90% 유지 도착률이 PolyServe(15.8)보다 **낮다** | 예상대로다. **거절도 결합도 없는 극단이 가장 낮다** | `motivation_v3.md` §3.1.1의 표에 다섯 번째 arm으로 넣고, §3.3.1의 A0×B2 칸을 숫자로 채운다 |
| Llumnix SLO(20.4)와 **PolyServe(15.8) 사이** | 거절 없는 것이 정적 파티션보다 나은 구간이 있다 | **§3.2.1의 "격리 대 혼합" 서술에 조건이 붙는다** — 정적 파티션이 가장 단순한 라우터보다 나쁠 수 있다는 뜻이므로, PolyServe를 "격리의 대표"로 세우는 자리에 그 사실을 같이 적는다 |
| **llm-d(18.7)보다 높다** | **예측 기반 라우팅이 round-robin보다 나쁘다는 뜻이다** | 그대로 적는다. `related-works-review.md` §8.3(LMetric)이 정확히 그 방향을 주장하므로 **우리 결과가 그 논문을 지지하는 것이 되고**, 그것은 우리 논문의 §3.2.2(요청 단위 예측기의 한계)를 강화한다 |
| 반복 간 폭이 5점을 넘는다 | 이 arm이 불안정하다 | 반복을 넷으로 늘리기 전에는 무릎을 인용하지 않는다 |

**반증 조건**: 이 arm이 **네 arm 중 가장 낮지 않으면**, "거절도 결합도 없는 것이 최악"이라는
서술을 못 쓴다. 그때는 §3.3.1의 A0 행 진단을 다시 써야 한다.

## 6. 이 기준선이 답하지 않는 것

- **거절이 없는 것과 결합이 없는 것을 가르지 못한다.** 둘 다 없으므로 어느 쪽이 얼마인지는 이
  arm으로 알 수 없다. 그것을 가르는 것은 우리 안의 ablation이다.
- **부하를 보는 것의 값을 재지 못한다.** 그러려면 Llumnix를 같은 워크로드에서 되살려서
  **round-robin(부하 안 봄) 대 Llumnix(부하 봄)**를 나란히 놓아야 한다. **둘 다 돌리면 그
  비교가 공짜로 생긴다** — 같은 칸의 두 arm이 아니라 **부하 인식 하나만 다른 쌍**이 된다.
  ⚠ 그러려면 Llumnix도 수정 후 워크로드에서 다시 돌려야 하고, 그것도 거절 0%라 §4의 유효성
  검사를 똑같이 받아야 한다.
- **production stack의 다른 라우팅 규칙은 안 잰다**(§7).

## 7. 나중에 따로 세울 것 둘

- **`prefixaware`** — 접두사 길이로 목적지를 고르는 규칙. **우리 prefix 회계와 축이 겹치므로**
  같은 실험에 넣으면 한 번에 두 가지를 바꾸는 것이 된다. 따로 세우면
  **"prefix를 목적지 선택에 쓰는 것 대 prefill 비용 회계에 쓰는 것"**이라는 깨끗한 대비가
  된다 — 우리 §3.4.2가 "prefix-aware routing이라고 쓰면 안 된다"고 적어 둔 바로 그 구분이다.
- **`kvaware`** — LMCache 컨트롤러를 요구한다. 배포가 무겁고 우리 엔진 구성과 맞는지 확인이
  먼저다.

## 8. 확인한 것과 확인 안 한 것

**확인했다** (2026-08-10, 소스를 받아서 읽었다):
production-stack의 라우팅 선택지 여섯과 Helm 기본값 `roundrobin`,
`RoundRobinRouter.route_request`가 엔진 통계를 안 쓴다는 것, PyPI `vllm-router`가 다른
물건이고 기본값이 `cache_aware`라는 것.

**확인 안 했다**: production stack이 우리 엔진 구성(vLLM V1, TP2 네 인스턴스)에 그대로 붙는지.
**우리는 그 스택을 배포하지 않고 알고리즘만 우리 스케줄러에 이식하므로 이 확인은 필요 없지만,
논문에 "we compare against the vLLM production stack"이라고 쓰면 안 되고 "we port its
round-robin routing logic"이라고 써야 한다.**
