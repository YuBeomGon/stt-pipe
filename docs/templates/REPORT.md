# Phase 3 Run Report — {{job_id}}

> 본 리포트는 `scripts/analyze_run.py` 가 자동 산출하는 8 개 축 (A~H) 양식이다.
> `{{변수}}` 자리는 자동 치환, `<!-- TODO: ... -->` 자리는 분석자가 채운다.
> 정본 흐름은 [`docs/PHASE2-PLAN.md`](../PHASE2-PLAN.md) §1 / §3.

생성 시각: {{generated_at}}
잡 wall clock: {{total_wall_clock}}
분석 대상 runs: {{n_iterations_seen}} 개 (`runs/<hyp_id>/` 디렉토리 기준)

---

## Portfolio Evolution (Step 1 — proposal §3/§5/§12)

> harness-derived family/signature 기준. self-declared family 와 무관.
> 정본 입력: `runs/_summary/<job>_decisions.jsonl` + `<job>_portfolio.json`.

{{portfolio_evolution}}

---

## A. 결과

| 항목 | 값 |
|------|-----|
| Best `corpus_cer` (`HarnessState.best_cer`) | {{final_corpus_cer}} |
| Best hyp_id | `{{best_hyp_id}}` |
| `target_cer` (수동 목표) | {{target_cer}} |
| `baseline_cer` (faster-whisper, 앵커) | {{baseline_cer}} |
| Target reached | {{target_reached}} |
| Noise floor σ | {{noise_floor_sigma}} (`is_provisional={{noise_floor_provisional}}`) |
| 채택 / 전체 iter | {{n_accepted}} / {{n_total_iter}} ({{accept_rate_pct}}%) |
| ROLLBACK | {{n_rollback}} |

### 학습 곡선

{{learning_curve}}

### 채택 시계열

{{accept_timeline}}

> `accept_timeline` 컬럼: iter / hyp_id / corpus_cer / Δ_from_prev_best / accepted / guard_violations.

### Holdout

자세히는 별도 [`HOLDOUT.md`](../runs/_summary/HOLDOUT.md) 참조.

- 잡 마지막 0715 `corpus_cer` : {{eval_cer}}
- 0813 holdout `corpus_cer`    : {{holdout_cer}}
- Δ (`holdout - eval`)          : {{holdout_delta}}
- Overfit 의심 (`|Δ| > 2σ`)     : {{holdout_overfit}}

---

## B. 하네스 구멍

### 자동 부분

가드 위반 유형별 카운트:

{{guard_violations_breakdown}}

| 의심 신호 | 자동 산출 |
|------|------|
| ROLLBACK 중 가드 위반 비율 | {{rollback_guard_violation_pct}}% (50%+ 면 임계 너무 빡빡) |
| `corpus_cer` 가 좋아졌는데 ROLLBACK 된 후보 | {{rollback_with_improvement}} |
| `quality budget` 임계 조정 후보 | {{threshold_warnings}} |

### 사람 판단

<!-- TODO: agent.stream / commit 메시지 검토 — 가드 우회 시도, 표면 막힘 흔적, 자기 보고 ↔ 실제 동작 일치 여부 -->

---

## C. 에이전트 시야

### 자동 부분

#### per-file CER 시계열 — 가장 큰 mover / mover 정체 파일

{{per_file_movers}}

#### `error_breakdown` (sub/del/ins ratio) 추이

{{error_breakdown_trend}}

#### `length_ratio` (mean / p05 / p95) 추이

{{length_ratio_trend}}

#### `hallucination_hit_rate` 추이

{{halluc_rate_trend}}

#### `diagnosis_report.json` focus_file 선정 추이

{{diagnosis_focus_trend}}

### 사람 판단

<!-- TODO: focus 파일이 반복적으로 노출됐는데 가설이 그 신호를 읽지 못한 케이스. 에이전트 자기 보고 vs 실제 메트릭 변화 -->

---

## D. 탐색 다양성

### A' candidate self-declared lane / fingerprint (정본)

candidate 가 매 iter `candidate_meta.json` 에 직접 선언한 lane / fingerprint
기반. A' (proposal 2026-05-29-agent-design) 도입 이후의 정본 다양성 신호.

| 자동 채택 lane 분포 (자기 선언) | 채택 수 |
|------|------|
| segmentation | {{lane_segmentation}} |
| decoding | {{lane_decoding}} |
| prompt | {{lane_prompt}} |
| postprocess | {{lane_postprocess}} |
| telemetry | {{lane_telemetry}} |

| 메트릭 | 값 | 판정 기준 (proposal §4) |
|------|------|------|
| Lane 분포 entropy (nats, max ln5 ≈ 1.609) | {{lane_entropy}} | > 1.2 = A' 단독 효과 충분 |
| 채택 fingerprint Jaccard 평균 거리 | {{fingerprint_jaccard_mean}} | > 0.5 = 동일 lane 내 다양 |
| 최장 동일-fingerprint streak (전체 iter) | {{max_fingerprint_streak}} | ≤ 2 = sweep 회피 |
| Format reject 비율 (`{{format_reject_count}} / {{n_attempted}} attempted`) | {{format_reject_pct}}% | < 20% calibration, > 40% profile 재작성 |

### Legacy heuristic (commit 메시지 + diff 키워드 — 비교용)

| 카테고리 | 채택 수 |
|------|------|
| chunking (`chunk\|window\|split\|segment`) | {{cat_chunking}} |
| prompt (`prompt\|token\|language`) | {{cat_prompt}} |
| decode (`beam\|temperature\|fallback\|sample`) | {{cat_decode}} |
| post (`dedup\|merge\|regex\|postprocess`) | {{cat_post}} |
| unclassified | {{cat_other}} |

- 마지막 5 iter 카테고리: {{recent_5_categories}}
- 80%+ 단일 카테고리 경고: {{concentration_warning}}

### 사람 판단

<!-- TODO: lane / fingerprint 자기 선언과 실제 diff 내용의 일치 여부 검토.
candidate 가 "decoding" 선언했는데 실제로는 segmentation 변경한 케이스 있는지 -->

---

## E. 메트릭 적절성

### 자동 부분

| 진단 | 값 |
|------|------|
| `|corpus_cer - macro_cer|` mean / max | {{cm_divergence}} |
| per-file CER (잡 마지막 채택) std / IQR | {{per_file_std}} / {{per_file_iqr}} |
| guard ↔ `corpus_cer` 피어슨 상관 | {{guard_cer_corr}} |
| `runtime_s_per_audio_min` 추이 | {{runtime_trend}} |

### 사람 판단

<!-- TODO: corpus 평균 뒤에 한두 파일이 점수를 끌고 갔는지 (분산 큼 = 비대표) — primary metric을 다음 잡에서 macro 또는 trimmed로 바꿔야 하는지 -->

---

## F. 비용 효율

| 항목 | 값 |
|------|------|
| 총 wall clock (첫 → 마지막 채택 commit) | {{total_wall_clock}} |
| 평균 iter 시간 | {{iter_avg_s}} s |
| p50 / p95 iter 시간 | {{iter_p50_s}} / {{iter_p95_s}} s |
| 채택당 wall clock | {{cost_per_accept}} s |
| Δcer per minute | {{delta_cer_per_min}} |

---

## G. Attribution

| 항목 | 값 |
|------|------|
| Top-3 가설의 전체 Δcer 점유 | {{top3_share_pct}}% |
| ROLLBACK 직후 CONTINUE 비율 (recovery) | {{recovery_rate_pct}}% |
| 최장 연속 ROLLBACK 길이 | {{max_rollback_streak}} |

Top-3 채택 가설:

{{top3_accepted}}

---

## H. Reasoning 품질

### 자동 보조 — commit 키워드 ↔ 메트릭 방향 일치

| 항목 | 값 |
|------|------|
| 자동 일치율 (`아래 표 일치 / 검사 가능 수`) | {{auto_alignment_pct}}% |
| 일치 케이스 수 | {{n_aligned}} |
| 불일치 케이스 수 | {{n_misaligned}} |

불일치 후보 (사람이 들여다볼 대상):

{{mismatch_list}}

### 사람 판단

<!-- TODO: 채택 commit 메시지 별로 의도(가설) ↔ 효과(메트릭 변화) 일치 여부. 자동 키워드 보조와 별개로 본문 의미 검토 -->

---

## 종합 결론 (사람)

<!-- TODO:
1. 8 개 축 종합 판정 — 잡이 성공/실패/부분 성공 중 어디인지, 어떤 축이 가장 약했는지
2. 다음 잡 권고 — 가드 임계 조정, scope 확장, primary metric 변경 등
3. 봉인 유지/갱신 결정 — baseline_cer 재측정 필요 여부, σ 재측정 필요 여부
-->
