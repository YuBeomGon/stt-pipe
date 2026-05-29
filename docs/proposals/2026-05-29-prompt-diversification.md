# Proposal — Candidate Prompt Diversification (Cold-restart Wildcard 외)

- 상태: **Draft (skeleton)** — 본문 핵심은 phase3_002 결과 후 채움
- 작성: 2026-05-29
- 선행: [`2026-05-29-agent-design.md`](2026-05-29-agent-design.md) (A' candidate runtime)
- 영향: `harness/prompts/candidate.md`, `harness/runner.py` (prompt 빌더)
- 트리거: 선행 RFC §2.2 의 deferred "cold-restart wildcard (M3)" 슬롯,
  RFC §4 판정 표의 phase3_002 lane entropy / streak 결과

---

## 1. 배경

선행 RFC §1.2 에서 phase3_001 의 "chunking 83%" 단조성을 *3 층 가설* 로 분리:

- **L1**: candidate 사고 부재 (역할·접근법 안내 없음)
- **L2**: 컨텍스트 오염 (HISTORY tail anchoring 등)
- **L3**: 다양성 강제 없음 (같은 fingerprint 반복 허용)

A' (proposal-1) 는 L1 단독 해결 — profile + YAML gate + lane round-robin
권고. L3 의 *iter 단위 다양성* 은 부분적으로 다룸 (fingerprint 기록, 자기-검증
체크리스트).

**누락된 것**: *메타 단위 다양성* — "지금까지 한 접근 전부 무효, 원점에서 재고"
류. L3 의 *깊은 부분* + L2 의 일부 (HISTORY 무게).

본 proposal 은 이 누락을 다룬다. 채택 시점은 phase3_002 결과의 lane
entropy / streak 가 RFC §4 판정 표의 어디에 떨어지는지에 따라.

---

## 2. 현재 있는 것 vs 누락된 것

### 2.1 현재 있는 *micro adapt* (lane 단위)

| 메커니즘 | 위치 | 효과 |
|---|---|---|
| Lane round-robin 권고 | `runner._suggested_lane(iter % 5)` | 매 iter 다른 lane 제안 |
| Reasoning checklist §1 lane saturation | `harness/prompts/candidate.md` | "권고 lane saturated 이면 less-tried 로 override" |
| Reasoning checklist §2 fingerprint duplication | 동일 | "최근 5 iter 와 구조 동일이면 다른 메커니즘 / lane 전환" |
| `why_different_from_last_5` YAML 필수 | profile + runner gate | 매 iter "왜 새로운지" 자기 정당화 강제 |

→ *lane 안* 또는 *iter 단위* 의 회피.

### 2.2 누락된 *meta restart* (잡 단위)

| 부재 메커니즘 | 의미 | 트리거 후보 |
|---|---|---|
| **Cold-restart wildcard** | best 가 N iter 정체 → 지금까지 접근 전부 무효화, 도메인 가설부터 재시작 | best 미갱신 streak ≥ 5 |
| **Lane-cluster jump** | 한 lane 군 반복 → 통째 폐기, 강제로 다른 lane 진입 | lane streak ≥ 3 (현재 권고 약함) |
| **HISTORY 망각 모드** | 직전 narrative 전부 무시, 빈 slate 에서 재출발 | 위 둘 중 하나 발동 시 동반 |
| **새 관점 메트릭** | 안 본 axis (speaker turn ratio, music ratio, SNR band 등) 로 diagnosis reframe | diagnosis 가 같은 metric 만 반복 지적할 때 |

→ 잡 단위 *진짜 redirect*. A' 의 권고만으로는 LLM 이 anchoring 에서 못 빠져나옴.

---

## 3. 후보 메커니즘 (skeleton, 본문은 phase3_002 후)

### 3.1 Cold-restart wildcard

```
# runner.build_candidate_prompt 의 추가:
if state.iters_since_best_update >= COLD_RESTART_THRESHOLD:
    prompt += COLD_RESTART_DIRECTIVE
    # HISTORY tail / best diagnosis 생략
    # candidate.md 의 "원점 재고" 섹션 활성화
```

- threshold 후보: 5, 7, 10 iter (phase3_002 streak 분포 봐서 결정)
- "원점" 의 정의: profile.md 의 5 lane 자체를 *재선택* + 지금까지 시도된
  fingerprint 회피 명시. baseline / target 만 유지

### 3.2 Lane-cluster jump

현재 round-robin 은 권고 — LLM 이 override 하면 같은 lane 연속 가능. *강제*
모드: `state.last_n_lanes` 가 동일하면 그 lane 자체를 *제외 set* 에 추가하고
나머지에서 선택.

- 권고 → 약한 강제 (override 시 페널티) → 강한 강제 (제외) 단계 설계

### 3.3 HISTORY 망각 모드

wildcard 발동 시 동반. `_history_tail` 호출 생략 또는 *최소화* (best line 만).
이전 narrative 의 anchoring 효과 차단.

### 3.4 새 관점 메트릭 reframe

diagnosis 생성 시점에 *지금까지 cite 되지 않은* metric 을 강제 노출. 예:
diagnosis 가 length_ratio / hallucination 만 반복 인용했으면, 다음 iter 의
diagnosis 는 speaker turn / SNR / music ratio 를 *반드시* 포함.

→ `judge/diagnosis.py` 와 runner 의 best_diagnosis 추출 로직에 영향.

---

## 4. 판정 — 언제 도입할까

phase3_002 결과 (RFC §4 판정 표 활용):

| phase3_002 결과 | 본 proposal 의 도입 |
|---|---|
| lane entropy > 1.2 AND streak ≤ 2 | **불필요** — A' 단독으로 충분, 본 proposal Rejected |
| 0.8 < entropy < 1.2 OR streak 3~4 | **부분 도입** — §3.1 cold-restart 만 |
| entropy ≤ 0.8 OR streak ≥ 5 | **전면 도입** — §3.1~3.4 단계적 |
| LLM 이 권고 lane 무시율 > 60 % | §3.2 lane-cluster jump 우선 |

판정 데이터:
- **lane / fingerprint per-iter 원자료**: `runs/<hyp_id>/candidate_meta.json`
  (A' YAML 메타 파싱 결과 정본). HISTORY.md 는 narrative 만 담아 lane 구조
  데이터는 *없음* — codex 3차 review F4 정정
- **집계**: `scripts/analyze_run.py` 의 D 축 산출 (lane entropy, fingerprint
  Jaccard 평균, max streak, format reject 비율) 및 `docs/reports/<job_id>_REPORT_*.md`

---

## 5. 대안 / 기각 가능성

- **(a) phase3_002 에서 미리 도입** — 기각. ablation 깨짐. A' 효과 검증 불가
- **(b) round-robin 을 처음부터 강제** — 기각 (선행 RFC §2.2 (d)). candidate
  사고력 측정 못 함
- **(c) 외부 randomness (seed-based wildcard)** — 후속 옵션. 본 RFC 의 §3 후
  잔존 문제 있으면 평가

---

## 6. 작업 계획 (TBD, phase3_002 후 확정)

- [ ] phase3_002 결과 분석 → 본 proposal 본문 채우기
- [ ] §4 판정 표로 도입 범위 결정
- [ ] threshold (cold-restart streak, lane-cluster 길이 등) 확정
- [ ] candidate.md 에 "Cold-restart mode" 섹션 신설
- [ ] runner.build_candidate_prompt 에 분기 추가
- [ ] analyze_run.py D 축에 "wildcard 발동 횟수 / 효과" 측정 추가
- [ ] phase3_003 (또는 phase4_001) 으로 효과 측정

---

## 7. 영향 받는 파일 (예상)

### 수정
- `harness/prompts/candidate.md` — Cold-restart 섹션 + reasoning checklist 확장
- `harness/runner.py` — `build_candidate_prompt` 분기, threshold 상수
- `harness/state.py` — `iters_since_best_update`, `last_n_lanes` 추적 추가
- `scripts/analyze_run.py` — wildcard 효과 메트릭
- `judge/diagnosis.py` — (§3.4 채택 시) metric reframe 로직

### 신규
- 없음 — 기존 파일 확장으로 충분

---

## 8. 가드레일 검토 (예상)

| 대상 | 변경 |
|---|---|
| candidate sandbox | 없음 — prompt 변경만, 권한 / 훅 동일 |
| state.py 직렬화 형식 | 신규 필드 (`iters_since_best_update` 등) — 하위 호환 명시 필요 |
| ablation 비교 | 본 proposal 도입 잡은 phase3_002 와 *직접 비교 불가* — 새 baseline 으로 |

---

## 9. 미결 사항

- COLD_RESTART_THRESHOLD 의 적정 값 — phase3_002 의 streak 분포 봐야 함
- lane-cluster jump 의 강제도 (권고 / 페널티 / 제외 set) — 단계적 도입 vs 강한
  default
- HISTORY 망각 시 *어디까지* 망각 — best line 만 vs 완전 0
- §3.4 새 관점 메트릭은 `judge/` 수정 필요 — Phase 3 보호 영역이라 proposal 별도
  검토 필요 (judge 변경은 평가 정의 변화 = 큰 결정)
