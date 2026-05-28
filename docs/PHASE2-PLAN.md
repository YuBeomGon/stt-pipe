# Phase 2 — 평가 인프라 구축 플랜

> **범위**: Phase 3 (autoresearch 실행) *진입 전* 에 분석 도구·홀드아웃 평가·리포트
> 템플릿을 모두 갖춰 둔다. 잡 끝나고 만들면 *관측한 결과에 맞춰 분석을 reverse-fit*
> 할 위험이 있어서 사전 구축이 원칙.

전제: Phase 1 DoD 통과 — `judge/`, `scripts/verify.sh`, `baseline/*.json` 존재 +
`score_report.json` / `per_file.jsonl` 스키마 확정.

---

## 1. 평가 8 개 축

Phase 3 종료 후 다음 8 개 축으로 잡을 평가한다. 각 항목은 *자동 산출* 또는 *사람 판단*
으로 분류.

| 축 | 무엇 | 자동/수동 |
|----|------|-----------|
| **A. 결과** | 최종 cer, target 도달 여부, CONTINUE/ROLLBACK 카운트, 학습 곡선, **holdout (0813) vs eval (0715) 차이** | 자동 |
| **B. 하네스 구멍** | 가드 위반율, 노출/숨김 우회 시도, 표면 막힘 흔적 | 자동 일부 + 사람 |
| **C. 에이전트 시야** | per_file 시계열, error_breakdown 추이, length_ratio 분포 변화 | 자동 |
| **D. 탐색 다양성** | 채택 가설의 카테고리 분포 (chunking/prompt/decode/post/fallback/merge) | 자동 (keyword 분류) + 사람 (수정) |
| **E. 메트릭 적절성** | corpus vs macro 발산, per-file 분산, guard ↔ cer 상관 | 자동 |
| **F. 비용 효율** | 총 wall clock, GPU 시간, 개선당 비용 (Δcer/iter, Δcer/min) | 자동 |
| **G. Attribution** | 큰 개선 기여 iter 수 (집중 vs 누적), ROLLBACK 후 회복 비율 | 자동 |
| **H. Reasoning 품질** | 채택 가설 commit 메시지 의도 vs 실제 메트릭 변화 일치 | 사람 (자동 키워드 보조) |

자동 산출 = `analyze_run.py` 가 담당. 사람 판단 = REPORT 템플릿의 빈칸으로 남고 분석자가
채움.

---

## 2. 산출물

```
scripts/
├── analyze_run.py            # runs/ 전체 분석 → REPORT.md 자동 산출
└── evaluate_holdout.py       # holdout 1 회 평가 + overfit 진단

tests/
├── test_analyze_smoke.py     # 합성 score_report.json 으로 analyze_run.py 검증
└── test_evaluate_holdout_smoke.py  # dry-run 모드 smoke

docs/
└── templates/
    └── REPORT.md             # 8 개 축 양식 (자동 칸 + 사람 칸)

runs/_summary/                # Phase 3 종료 시 산출물 위치
├── REPORT.md                 # analyze_run.py 가 작성
└── HOLDOUT.md                # evaluate_holdout.py 가 작성
```

---

## 3. `scripts/analyze_run.py`

### 3.1 입력

- `runs/<hyp_id>/score_report.json` 전체
- `runs/<hyp_id>/per_file.jsonl` 전체
- `runs/<hyp_id>/_telemetry/*.jsonl` (있을 때)
- `baseline/target_cer.json`, `baseline/noise_floor.json`
- `assets/audio_profile/AIG_녹취반출_20250715.json` — 사후 분석 단서 (긴 무음/짧은 발화 구간 매칭)
- `git log --all` (commit 메시지, revert 흔적, timestamp)

> 본 도구는 **사후 분석 컨텍스트** — autoresearch agent (workspace 진화) 와 권한 분리.
> assets/audio_profile/ 읽기 허용. agent 의 workspace 에서는 차단 (AGENTS §1, PHASE3 §1.2).

### 3.2 CLI

```
python scripts/analyze_run.py \
  --runs-dir runs/ \
  --baseline baseline/ \
  --template docs/templates/REPORT.md \
  --out runs/_summary/REPORT.md
```

### 3.3 산출 항목 (축별 자동 부분)

#### A. 결과
- `final_corpus_cer`, `target_cer`, `target_reached: bool`
- 채택 시계열: `[iter, hyp_id, corpus_cer, accepted: bool, guard_violations: list]`
- ASCII 또는 PNG 학습 곡선 (matplotlib 없이도 markdown table 가능)
- 채택률: `n_accepted / n_total_iter`

#### B. 하네스 구멍 (자동 부분)
- 가드 위반 유형별 카운트: `{hallucination: n, empty_output: n, length_ratio_p05: n, ...}`
- ROLLBACK 중 가드 위반 비율 — 50% 초과면 *임계 너무 빡빡* 경고
- corpus_cer 가 좋아졌는데 가드로 ROLLBACK 된 케이스 — *가드 임계 재조정 후보*

#### C. 에이전트 시야 (자동 부분)
- per_file CER 시계열 — 어떤 wav 가 가장 큰 개선, 어떤 wav 가 정체
- `error_breakdown` 추이 (sub_ratio / del_ratio / ins_ratio 의 iter 별 변화)
- length_ratio mean / p05 / p95 시계열
- hallucination_hit_rate 추이

#### D. 탐색 다양성 (자동 부분)
채택된 commit 의 메시지·diff 에서 키워드 매칭으로 1차 분류:
- chunking: `chunk|window|split|segment`
- prompt: `prompt|token|language`
- decode: `beam|temperature|fallback|sample`
- post: `dedup|merge|regex|postprocess`

카테고리별 채택 수 + 마지막 5 iter 의 카테고리 (한쪽 쏠림 검출). 80%+ 단일 카테고리
경고.

#### E. 메트릭 적절성
- `|corpus_cer - macro_cer|` 시계열 — 발산이 커지면 한두 파일이 점수를 끄는 신호
- per-file CER 의 표준편차 / IQR
- guard 값과 corpus_cer 의 iter-단위 상관 (피어슨 등)
- `runtime_s_per_audio_min` 변화 — 속도-정확도 trade-off

#### F. 비용 효율
- 총 wall clock (첫 commit ↔ 마지막 commit timestamp)
- 평균 iter 시간, 분포 (p50/p95)
- 채택당 비용: `wall_clock / n_accepted`
- Δcer 당 비용: `(initial_cer - final_cer) / wall_clock_min`

#### G. Attribution
- 채택된 가설의 Δcer 정렬 — top 3 가 전체 개선의 몇 % 인지 (Pareto)
- ROLLBACK 직후 CONTINUE 비율 (recovery rate)
- 연속 ROLLBACK 최대 길이 (stuck 구간)

#### H. Reasoning 품질 (자동 보조)
- 채택 commit 메시지에서 키워드 추출 (chunking/hallucination/long-form 등)
- 해당 키워드에 대응하는 메트릭이 실제 그 방향으로 움직였는지 자동 비교 (예: "환각 제거" → ins_ratio 감소 여부)
- 일치/불일치 카운트 + 불일치 사례 리스트 (사람이 들여다볼 후보)

### 3.4 출력

`runs/_summary/REPORT.md` — 템플릿 (§5) 의 자동 칸을 채운 markdown. 사람 판단 칸은
`<!-- TODO -->` 로 남김.

### 3.5 안 하는 것

- 단일 메트릭 ranking 산출 X (8 개 축 종합 판정은 사람이 함)
- 자동 결정 (다음 잡 재실행 등) X — 분석만

---

## 4. `scripts/evaluate_holdout.py`

### 4.1 동작

1. **잠금 확인**: `runs/_summary/JOB_DONE.lock` 또는 동등 마커가 있어야 진행. 진행 중인
   autoresearch 잡 있으면 거부.
2. holdout chmod 복구 (`u+rwX`)
3. 현재 best `workspace/transcribe.py` 로 0813 13 페어 evaluate
   - `judge/evaluate.py` 와 동일 코드 경로
   - 결과: `runs/holdout_<ts>/score_report.json`
4. 0715 (잡 마지막 채택) 결과와 비교
   - `Δcorpus_cer = holdout_cer - eval_cer`
   - per-batch 가드 비교
5. `runs/_summary/HOLDOUT.md` 작성:
   - `eval_cer`, `holdout_cer`, `Δcer`, overfit 판정 (`|Δcer| > 2σ` 시 의심)
   - per-file CER 비교
   - 가드 차이

### 4.2 CLI

```
python scripts/evaluate_holdout.py [--unseal] [--dry-run]
```

- `--dry-run`: chmod 복구 없이 코드 경로만 smoke
- `--unseal`: chmod 복구 단계 포함 (기본은 거부 — 명시 필요)

### 4.3 사후 처리

평가 후 chmod 다시 000 으로 봉인 (재호출 차단). 필요하면 사람이 명시적으로 복구.

---

## 5. `docs/templates/REPORT.md`

8 개 축 양식. 자동 칸은 `{{변수}}`, 사람 칸은 `<!-- TODO: ... -->`. analyze_run.py 가
변수 치환.

골자:

```markdown
# Phase 3 Run Report — {{job_id}}

생성 시각: {{generated_at}}
잡 wall clock: {{total_wall_clock}}

## A. 결과
- Target reached: {{target_reached}} ({{final_corpus_cer}} vs {{target_cer}})
- 채택률: {{n_accepted}}/{{n_total_iter}}
- 학습 곡선: {{curve_table}}

## B. 하네스 구멍
- 가드 위반 분포: {{guard_violations_breakdown}}
- 임계 의심: {{threshold_warnings}}
- 우회 시도 (사람 판단): <!-- TODO: agent.stream 검토 -->

## C. 에이전트 시야
- 최대 개선 파일 / 정체 파일: {{per_file_movers}}
- error_breakdown 추이: {{breakdown_table}}
- length_ratio 추이: {{lr_table}}

## D. 탐색 다양성
- 카테고리 분포: {{category_distribution}}
- 쏠림 경고: {{concentration_warning}}
- 사람 분류 보정: <!-- TODO: diff 검토 후 카테고리 수동 재분류 -->

## E. 메트릭 적절성
- corpus vs macro 발산: {{cm_divergence}}
- per-file 분산: {{per_file_std}}
- guard ↔ cer 상관: {{guard_cer_corr}}

## F. 비용
- iter 평균 시간: {{iter_avg_s}} (p95: {{iter_p95_s}})
- Δcer per min: {{delta_cer_per_min}}

## G. Attribution
- Top-3 가설의 전체 Δcer 점유: {{top3_share}}%
- ROLLBACK 후 회복률: {{recovery_rate}}
- 최장 ROLLBACK streak: {{max_rollback_streak}}

## H. Reasoning 품질
- 키워드-메트릭 자동 일치율: {{auto_alignment}}
- 불일치 후보: {{mismatch_list}}
- 사람 판정: <!-- TODO: 각 채택 가설별 의도 vs 효과 검증 -->

## Holdout
(별도 `HOLDOUT.md` 참조)

## 종합 결론 (사람)
<!-- TODO: 8 개 축 종합 판정 + 다음 잡 권고 (가드 임계 조정, 표면 확장, 등) -->
```

---

## 6. 합성 데이터 smoke test

`tests/test_analyze_smoke.py`:
- 가짜 `runs/<hyp_id>/score_report.json` 5~10 개 생성 (cer 추이 + 가드 + 카테고리 다양하게)
- `analyze_run.py` 호출 → REPORT.md 생성 → 변수 치환 모두 성공, 카테고리 분포 합산 = 100% 확인
- 단조 감소·plateau·regression 패턴 각 1 개씩 케이스 추가

`tests/test_evaluate_holdout_smoke.py`:
- `--dry-run` 모드 호출 → chmod 호출 없이 코드 경로 통과
- 잠금 파일 없는 상태에서 거부되는지 확인

---

## 7. Phase 2 DoD

- [ ] `scripts/analyze_run.py` 합성 데이터 smoke 통과
- [ ] `scripts/evaluate_holdout.py` dry-run smoke 통과
- [ ] `docs/templates/REPORT.md` 8 개 축 변수 전부 정의됨
- [ ] `pytest tests/test_analyze_smoke.py tests/test_evaluate_holdout_smoke.py` 통과
- [ ] 사람 판단 칸 (B 우회, C 추론, D 분류, H reasoning) 이 템플릿에 명시
- [ ] git commit (Phase 2 산출물 일괄)

---

## 8. 안티 패턴

- 잡 끝나고 산출된 결과 보고 그제야 analyze_run.py 만들기 (분석이 결과에 맞춰짐)
- 8 개 축을 단일 점수로 환원해 자동 판정 (사람 판단 항목을 자동으로 처리)
- REPORT 템플릿의 사람 칸을 자동 채움 (의도/효과 일치 같은 판단은 자동화 X)
- 합성 smoke 없이 실제 잡 결과로 처음 돌리기
- holdout 평가를 잡 도중 호출
- evaluate_holdout.py 에서 chmod 복구만 하고 재봉인 안 하기

---

## 9. 다음 단계

Phase 2 DoD 통과 → [`PHASE3-PLAN.md`](PHASE3-PLAN.md) 으로 진행 (autoresearch 실행 +
잡 종료 후 본 인프라 적용).
