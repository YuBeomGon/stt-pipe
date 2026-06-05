# Step 1 Code Map — Visibility + low-risk policy (TEMP, 완료 후 삭제)

> Proposal §15 Step 1 구현 매핑. 이 폴더(`docs/_workmap/`)는 작업용 임시.
> Step 1 끝나면 통째로 삭제.

## Step 1 범위 (proposal §15)

- mode metadata 저장
- `best_raw` / `micro_bank` 저장
- harness-derived signature/family MVP  ← **keystone**
- REPORT 에 family/mode/micro_bank 지표 추가
- `decisions.jsonl` append-only trace
- explore floor 보수적 조정 (scheduler 전이라 최소 변경만)

**불변식**: 기존 keep/reject 판정 로직(`policy.decide_candidate`)은 건드리지 않는다.
Step 1 은 전부 **additive (기록·계산만 추가)**. 동작 변경은 Step 2+ 에서.

---

## 현재 코드 사실 (grep 확인됨)

### 루프 / 상태
- `harness/runner.py::run_iteration` (L1228~1466) — iter 1회. 끝에 `append_event`
  → `state.save` → `commit_iteration`.
- `harness/runner.py::commit_iteration` (L1188) → `_persist_candidate_meta`
  (L1141) 가 `runs/_summary/<job>_candidate_meta.jsonl` 한 줄 append 후 git add.
- `harness/state.py::HarnessState` — `best_cer/best_hyp_id/iters_since_best_update`.
  `load()` 는 unknown key 무시 (전방호환 OK → 필드 추가 안전).
- `RunnerConfig` (frozen dataclass) — `summary_dir=runs/_summary`,
  `runs_dir=runs`, `allowed_path=workspace/transcribe.py`.

### candidate 자기보고 (이미 있음 — 신뢰 기준 아님)
- `parse_candidate_metadata` (L817) → 필수 키 `capability_investigated /
  what_i_learned / hypothesis / fingerprint`. `fingerprint` = list[str] ≤6.
  `lane` optional. → `candidate_meta.json` 기록.

### judge 산출 (metric_best 축 출처 — §14 확인됨)
`judge/metrics.py::corpus_aggregate` (L121) 가 `score_report.json` 에 쓰는 키:
```
corpus_cer, macro_cer,
error_breakdown.{sub_ratio,del_ratio,ins_ratio},
length_ratio.{mean,p05,p95},
repeated_text_rate, audio_coverage_rate,
hallucination_hit_rate, hallucination_hits_total,
total_inference_time_s, runtime_s_per_audio_min, avg_rtf,
empty_output_rate, num_files, num_files_scored
```
→ metric_best 축 매핑:
- `best_coverage` = `length_ratio.mean` 1.0 근접 + `error_breakdown.del_ratio` 최소
- `best_substitution` = `error_breakdown.sub_ratio` 최소
- `best_low_hallucination` = `hallucination_hit_rate` + `repeated_text_rate` 최소
- `fast_runtime_variant` = `total_inference_time_s` 최소 (guard 통과 전제)

### REPORT
- `scripts/analyze_run.py` — `runs/<hyp_id>/` 에서 score_report/per_file/meta 수집,
  `IterRecord` 로 모음. 기존 다양성 proxy: `_fingerprint_jaccard_mean` (L592),
  `_max_fingerprint_streak` (L622), `_lane_distribution`/`_lane_entropy` (L550/567).
  → **이건 self-declared fingerprint 기반.** 신규 harness-derived family 지표로 보강.

---

## Step 1 변경 매핑

### 1. `harness/signature.py` (신규 — 순수모듈, runner 안 건드림) ★keystone
프로포절 §5.2/§5.3.
```
extract_features(diff_text: str, meta: dict) -> Features
  - touched_regions  : diff hunk 의 수정 함수/블록 이름 (정규식 @@ ... def/heading)
  - api_keywords     : 고정 vocab 매칭 (return_scores/num_hypotheses/align/
                       detect_language/no_speech/temperature/beam_size/patience/
                       suppress/timestamp/compression ...)
  - changed_params   : diff 추가줄의 kwarg= / 상수명
  - stage_tokens     : segmentation/audio_frontend/confidence_gate/rerank/
                       postprocess/vad/normalize 매핑
compute_signature(features) -> "sig_xxxx"   # normalized tuple 의 안정 hash
assign_family(features, known_families) -> (family_id, is_new)
  # feature-set Jaccard ≥ threshold → 기존 family, 아니면 family_NNN 신규
  # threshold 보수적 (false-split 선호). default 0.5.
```
- **테스트 먼저** (`tests/test_signature.py`): 같은 diff→같은 sig; 무관 diff→다른
  family; 유사 diff→같은 family; rename 만 한 diff→Jaccard 로 흡수.
- 결정성: hash 는 `hashlib.sha1(repr(sorted(tuple)))[:8]` (Math.random 류 금지).

### 2. `harness/portfolio.py` (신규) — proposal §3
```
@dataclass Portfolio:
  job_id; updated_at_iter; global_best; family_best:{}; metric_best:{};
  micro_bank:[]; rejected_promising:[]
  load/save (state.py 와 동일 atomic write 패턴)
  update(iter_record) -> 변경된 slot 목록   # 어느 slot 갱신됐는지 반환(로그용)
```
- `runs/_summary/<job>_portfolio.json`.
- Step 1 에선 **저장만** (prompt 주입·parent 선택은 Step 2). global_best 갱신은
  기존 keep 결정을 그대로 미러링.

### 3. `harness/state.py` — micro_bank 판정 헬퍼 (additive)
- 신규 필드 불필요 (portfolio.json 이 보관). 단 `decide`-후 micro_bank 여부를
  계산하는 **순수함수**를 policy 옆에 둠:
```
harness/policy.py::is_micro_bank(report, best_report) -> (bool, reason)
  # strict improvement(Δcer>0 이지만 keep threshold 미만) OR 한 축 개선
  # (sub/del/coverage/hallucination 중 하나 best 갱신). keep 로직은 불변.
```

### 4. `decisions.jsonl` writer — proposal §12.1
- `harness/runner.py` 에 `_persist_decision(config, record)` 추가 →
  `runs/_summary/<job>_decisions.jsonl` append. `commit_iteration` 의 paths 에 추가.
- Step 1 단계 필드 (scheduler 전이라 pre-decision 은 단순):
```json
{"iter":N,"hyp_id":...,"policy_version":"portfolio_v1",
 "scheduled_mode":null,"chosen_mode":null,           // Step3 전까진 null
 "harness_signature":"sig_..","harness_family_id":"family_..",
 "final_decision":"keep|micro_bank|reject",
 "decision_reason":"...","cer":0.x,
 "axis_metrics":{...}, "portfolio_slots_updated":[...]}
```
- 한 곳에서: `run_iteration` 끝(L1447~) decision 확정 직후 + 각 early-reject
  분기. best-effort, 절대 commit path 에 raise 금지 (_persist_candidate_meta 패턴).

### 5. `scripts/analyze_run.py` — REPORT 지표 (proposal §12)
- `decisions.jsonl` 집계 함수 추가: `family_count`(harness family distinct),
  `family_best_table`, `mode_distribution`/`mode_success_rate`(Step3 후 의미),
  `micro_bank_count`, `repeated_failed_family_count`, `iter_to_0.20/0.18/0.16`,
  `portfolio_usage`.
- `family_count` 옆에 "diversity upper-bound estimate" 주석 출력 (§5.3).
- 기존 self-declared fingerprint 지표는 유지(비교용), family 지표를 정본으로 표기.

### 6. `harness/config.py` — explore floor (최소 변경)
- scheduler 가 아직 없으므로 **값만 보수화 검토**: `EXPLORE_RATIO_FLOOR 0.5→0.3`
  (005 회고 #3). Step 1 에선 이 한 줄만. 모드 schedule 은 Step 3.

---

## 구현 순서 (Step 1 내부)
1. `signature.py` + `tests/test_signature.py` (TDD, 격리)  ← 먼저
2. `portfolio.py` + `policy.is_micro_bank` + 단위테스트
3. runner 에 `_persist_decision` + portfolio.update 배선 (additive)
4. `analyze_run.py` 집계 + REPORT 템플릿
5. config explore floor 0.3
6. **smoke 3~5 iter** (전체 50회 아님) → decisions.jsonl/portfolio.json/REPORT
   채워지는지만 확인 → 데이터 보고 Step 2/3 설계

## 검증
- 기존 테스트 전부 green 유지 (keep/reject 불변 확인).
- `tests/test_signature.py` 신규 케이스 green.
- smoke 후 `runs/_summary/phase3_006_decisions.jsonl` 육안 확인.
