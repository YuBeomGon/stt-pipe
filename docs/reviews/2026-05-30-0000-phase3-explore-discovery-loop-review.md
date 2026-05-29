# Review — 발견형 candidate 루프 (intent-aligned)

| | |
|---|---|
| 작성 | 2026-05-30 00:00 KST |
| 리뷰어 | Claude (operator interactive session) |
| 대상 커밋 | `776101e` (branch `phase3-explore`) |
| 대상 파일 | `harness/runner.py`, `harness/state.py`, `harness/prompts/candidate.md`, `scripts/analyze_run.py`, `tests/test_harness_runner.py` |
| 선행 의도 | [`docs/proposals/2026-05-29-prompt-diversification.md`](../proposals/2026-05-29-prompt-diversification.md) §2~4 |
| 테스트 | `pytest tests/` → 114 passed (py11) |

---

## 0. 리뷰 기준 (운영자 의도)

> 후보 루프가 **local minimum(현 best 0.169)에 갇혀 잔손질만 반복하지 않고**,
> 다양한 접근을 **탐색**해 진짜 더 낮은 best CER 에 도달하게 한다. 수단은 "다양한
> 프롬프팅".

본 리뷰는 *버그 헌팅이 아니라* "이 변경이 위 의도를 달성하는가" 를 본다. 기계적
정확성(스키마 3각 정합, off-by-one, 예외경로)은 `/code-review` 의 몫으로 분리.

판정 요약: **방향은 맞으나, 의도를 절반만 달성.** local-min 회피를 막는 구조적
구멍 2개(🔴)가 변경 *바깥* 에 그대로 남아 있고, 탐색 강제 약화(🟠) 가 새로 생겼다.

---

## 1. Findings

### 🔴 F1 — findings ledger 가 "compound" 한다 했으나 최근 5개만 본다

- **증거**: `harness/runner.py:61` `_LEDGER_DEPTH = 5`; `:579-583`
  `build_candidate_prompt` 가 `_recent_iters(..., n=_LEDGER_DEPTH)` 결과로만
  ledger 를 만든다 (`_format_findings_ledger(recent)`).
- **의도 충돌**: proposal §3.3 은 "발견이 코드 롤백을 넘어 **복리로 쌓인다**" 가
  핵심. 그러나 슬라이딩 윈도우 5라 iter10 시점이면 iter1~5 의 표면 사실을 잊는다.
  25 iter 탐색에서 *표면 지도가 누적되지 않으면* 발견형의 동력이 절반만 산다.
- **아이러니**: 영속 jsonl(`runs/_summary/<job>_candidate_meta.jsonl`) 에 전량을
  이미 적고 있는데(§7 보존), 정작 프롬프트는 그 jsonl 을 **소비하지 않고** last-5
  디렉토리를 다시 읽는다. 보존만 하고 활용 안 함.
- **권고**: ledger 를 jsonl 전체(또는 충분히 큰 N, dedup 후)에서 빌드. dedup
  table 은 최근 윈도우로 두되 "학습된 사실" 은 누적 노출.

### 🔴 F2 — keep 게이트(Δ≥0.01)가 best CER 달성을 직접 막는다 (미해결)

- **증거**: `harness/runner.py:1036` `state.record_best(...)` 는 decision 이
  `keep|success` 일 때만 호출. 게이트는 `decide_candidate`(policy)의 Δ≥
  `absolute_delta_fallback`(0.01). `harness/state.py:50-53` `record_best` 만
  `iters_since_best_update` 를 0 으로 리셋.
- **의도 충돌**: phase3_002 에서 iter18=0.161 이 best(0.169)를 raw 로 깼지만 Δ<
  0.01 로 기각 → **0.161 을 best 로 lock-in 못 함.** "best CER 얻기" 가 목표인데
  시스템이 작은 실질 개선을 banking 하지 못한다. 게다가 같은 이유로
  `iters_since_best_update` 도 리셋 안 되어 0.161 을 "개선 없음" 으로 본다.
- **범위 주의**: 이번 커밋은 *프롬프트 다양화* 만 건드렸고 게이트/policy 는 그대로.
  즉 "후보가 더 다양한 걸 제안" 해도 harness 가 더 나은 raw 결과를 버리는 구조는
  미변경. proposal §3.4 에 옵션으로만 적고 **구현 안 함** → 의도와 가장 큰 갭.
- **권고**: (a) 같은 방향 sub-threshold 이득 N회 연속 누적 채택, 또는 (b) `best_raw`
  를 별도 추적해 job 종료 시 best_raw 를 산출물로, 또는 (c) plateau 후반 게이트
  완화(2σ→1σ). policy/결정 로직 변경이라 별도 작은 RFC 가치.

### 🟠 F3 — 강제 다양성을 제거하고 자발적 다양성만 남김 (stall 전 구간 약화)

- **증거**: round-robin(`_suggested_lane`) 제거됨. `parse_candidate_metadata`
  (`harness/runner.py:~485`)는 fingerprint 형식만 검증하고 **최근과의 중복을
  거부하지 않는다.** 프롬프트(`candidate.md` Required output / Anti-patterns)는
  "반복하지 마" 를 *권고* 만.
- **의도 충돌**: cold-restart 는 `iters_since_best_update >= 5`(`runner.py:587`)
  에서만 발동. 그 *전* 구간엔 beam=5→6→7 식 잔손질을 막을 hard 장치가 0. local-min
  회피가 목표면 stall 직전까지의 구간이 오히려 이전(round-robin)보다 약해졌다.
- **권고**: 중복 fingerprint hard-reject(이미 dedup 정보는 recent table 에 있음),
  또는 `_COLD_RESTART_THRESHOLD` 를 5 미만으로, 또는 둘 다.

### 🟠 F4 — 발견이 성립하려면 후보가 실제로 탐사할 수단이 필요

- **증거**: production hardening `harness/runner.py:81`
  `--disallowedTools=Bash,WebFetch,WebSearch,Task`. candidate.md 는 "frozen 읽고
  라이브러리 API 를 떠올려라" 로 정찰을 지시.
- **의도 충돌**: Bash 차단 시 후보는 `help()`/`dir()` 로 **경험적 확인 불가**,
  오직 학습지식 의존. 모델이 `model.align`/`return_no_speech_prob` 의 존재를
  기억 못 하면 "발견" 이 멈춘다. proposal §3.5(probing)가 진짜 enabler 인데
  production 에서 꺼져 있음.
- **미결**: "claude -p 할 때 가는 것 제거" 가 Bash 복원이었는지 확인 필요. 아니라면
  read-only Bash 허용(가드 유지하 holdout/forbidden deny) 이 프롬프트 문구보다
  발견 폭에 더 결정적일 수 있음.

### 🟡 F5 — 다양성 *관측력* 저하

- **증거**: lane 을 선택 태그로 강등(검증 제거). `scripts/analyze_run.py` 의 lane
  entropy 는 후보가 lane 을 안 달면 n/a.
- **영향**: proposal §5 판정이 쓰던 lane entropy 지표를 잃고 fingerprint Jaccard
  하나에 의존. 측정만의 문제(루프 동작엔 무해)지만, "다양해졌나" 를 사후 검증하기
  어려워짐.
- **권고**: lane 을 계속 *권장*(선택이되 강하게 유도)하거나, capability_investigated
  텍스트에서 자동 클러스터링으로 다양성 지표 대체.

---

## 2. 의도에 잘 맞는 부분

- **발견형 framing + `what_i_learned` 강제**: 매 iter "표면을 한 조각 더 알아내라"
  압력 → 잔손질 대신 탐사 (정확한 방향).
- **cold-restart 강제 모드**: stall 시 "파라미터 튜닝 dead, 구조 변경 허용,
  메커니즘은 직접 찾아라" + HISTORY 망각 → basin 탈출 압력(F1/F2 가 받쳐주면 강력).
- **frozen 표면 지목**: 미사용 lever(align/score/no_speech/fallback)가 실제로 거기
  있고, holdout overfit(반복/환각) 갭과도 정합.
- **영속 jsonl(§7)**: 분석 데이터 durable — 단 F1 처럼 *소비* 가 빠짐.

---

## 3. 권고 액션 (우선순위)

1. **F1 + F2 먼저** — 의도(best CER + local-min 회피)에 직접 묶인 두 구멍.
   - F1: ledger 를 `<job>_candidate_meta.jsonl` 전체에서 빌드.
   - F2: best_raw 추적 또는 sub-threshold 누적 채택 (policy 결정 — 별도 RFC).
2. **F3** — 중복 fingerprint hard-reject 또는 threshold 하향.
3. **F4** — candidate 의 Bash/probing 정책 확정 (운영자 결정).
4. **F5** — 다양성 관측 지표 보강 (후속).
5. 그 후 `/code-review high` 로 기계적 정확성 패스, 이어서 phase3_003 측정.

---

## 4. 메모

- 본 리뷰는 설계-의도 정렬 관점. 스키마 3각 정합/예외경로/off-by-one 등 기계적
  정확성은 `/code-review` 로 별도 확인 권장 (층이 다름).
- F2 는 이번 커밋 범위 밖(policy)이지만, 의도 달성에 가장 큰 레버라 명시.
