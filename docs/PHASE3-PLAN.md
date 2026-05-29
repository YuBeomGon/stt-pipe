# Phase 3 — 자체 Harness 실행 플랜

> 범위: Phase 1·2 산출물이 준비된 뒤, repository 안의 `harness/` controller가
> `workspace/transcribe.py` 후보를 검증하고 keep/reject/success를 판정하는 운영 절차.
> 진행 상태와 DoD 체크박스는 [`PHASE3-STATUS.md`](PHASE3-STATUS.md)에만 둔다.

정본 관계:
- 도메인·metric·금지사항: [`STT-PIPELINE-SPEC.md`](STT-PIPELINE-SPEC.md)
- 시스템 구조: [`DESIGN.md`](DESIGN.md)
- 문서 지도: [`SSOT.md`](SSOT.md)
- loop 그림: [`PHASE3-LOOP.md`](PHASE3-LOOP.md) (보조)

---

## 1. 목표

한국어 보험 콜센터 0715 eval 11파일에 대해 `workspace/transcribe.py`의
`transcribe(audio, sr) -> str` 파이프라인을 개선한다.

| 항목 | 기준 |
|------|------|
| Primary metric | `corpus_cer` lower-is-better |
| 최종 성공 | `corpus_cer <= baseline/target_cer.json:target_cer` |
| 최종 시간 | `total_inference_time_s <= baseline/target_cer.json:total_inference_time_s` |
| 비교 앵커 | `baseline_cer`는 거리감 참조, 성공 기준 아님 |
| 중간 채택 | 현재 best 대비 noise threshold 이상 개선 |
| 평가 근거 | `runs/<hyp_id>/score_report.json` |
| 원인 추론 | `runs/<hyp_id>/diagnosis_report.json`, `per_file.jsonl`, `_telemetry/` |

---

## 2. 코드 경계

| 영역 | 책임 |
|------|------|
| `harness/` | Phase 3 controller 본체. guard, policy, state, history, runner |
| `scripts/` | 사람이 실행하는 얇은 CLI와 일회성 운영 명령 |
| `judge/` | 평가자. 후보 텍스트를 점수와 diagnosis로 변환 |
| `workspace/transcribe.py` | 후보가 수정하는 유일한 STT pipeline 표면 |
| `frozen/` | CT2 + Whisper-large-v3-turbo backend 봉인 |
| `runs/` | iteration 산출물과 summary |

판단 기준:
- import 가능한 재사용 로직은 `harness/`에 둔다.
- 사람이 터미널에서 실행하는 entrypoint는 `scripts/`에 둔다.
- metric 산출은 `judge/`, 채택 판정은 `harness/`가 맡는다.

---

## 3. Phase 3 진입 가드

진입 전에 사람이 한 번 확인한다.

1. holdout 디렉토리 접근 차단: `scripts/seal_holdout.sh`
2. `workspace/transcribe.py` 외 후보 편집 금지
3. `frozen/`, `judge/`, `baseline/`, `assets/audio_profile/` 보호
4. `baseline/target_cer.json`, `baseline/noise_floor.json` 재측정 금지
5. 정상 verify 1회와 의도적 위반 smoke 1회

자체 harness 전환 뒤에는 `/autoresearch` 호출, autoresearch skill 설치, TSV 분석을
운영 전제로 삼지 않는다. 이전 조사 기록은 [`AUTORESEARCH.md`](AUTORESEARCH.md)에
historical 문서로 보존한다.

---

## 4. Iteration 흐름

한 iteration은 다음 순서를 따른다.

1. 후보 변경 생성 (`claude -p`를 candidate worker로 사용)
2. `workspace/transcribe.py` 정적 금지 패턴 검사
3. `judge.evaluate` 실행
4. `harness.guards`로 산술·catastrophic·runtime·quality budget 검사
5. `score_report.json`에서 `corpus_cer` 읽기
6. `harness.policy`가 keep/reject/success 판정
7. keep이면 best 갱신과 기록
8. reject면 후보 변경 rollback
9. `runs/_summary/HISTORY.md` append

기본 실행 형태:

```bash
python3 scripts/evolve.py \
  --job-id phase3_001 \
  --iters 25 \
  --candidate-cmd "claude -p" \
  --commit-results
```

수동 후보를 평가할 때는 candidate 생성 없이 현재 `workspace/transcribe.py`를 검증한다.

```bash
python3 scripts/evolve.py --job-id manual_check --iters 1 --manual
```

`--candidate-cmd`는 harness가 만든 prompt를 마지막 argv로 붙여 실행한다. 따라서
`claude -p`는 매 iteration마다 후보를 만드는 worker일 뿐이고, 검증·판정·기록·rollback은
repository 내부 `harness/`가 결정한다.

2회 이상 반복할 때는 `--commit-results`를 필수로 둔다. keep된 후보를 git 기준점으로
고정해야 다음 reject 때 직전 best 상태로 안전하게 돌아갈 수 있기 때문이다.

---

## 5. Guard 정책

Hard fail:
- `workspace/transcribe.py`의 backend 직접 import 또는 `from_pretrained` 사용
- `workspace/transcribe.py`의 `assets`, `audio_profile`, `silero` 직접 참조
- `judge.evaluate` 실패 또는 `score_report.json` 누락
- `Σ edits / Σ ref_chars`와 `corpus_cer` 불일치
- `empty_output_rate > 0.50`
- `length_ratio.p05 < 0.10`
- `length_ratio.p95 > 5.0`
- `total_inference_time_s > baseline_time * RUNTIME_HARD_MULTIPLIER`

Quality budget:
- hallucination, repetition, length, coverage가 baseline guard 분포보다 크게 악화되면
  기본은 warning이다.
- `QUALITY_BUDGET_HARD=1`이면 quality budget 위반도 hard fail로 처리한다.

초기 runtime 값:
- hard cap: `RUNTIME_HARD_MULTIPLIER=3.0`
- success cap: baseline time 이하

---

## 6. 채택 정책

채택 판단의 입력:
- 현재 후보 `score_report.json`
- 현재 best `corpus_cer`
- `baseline/noise_floor.json:sigma`
- provisional sigma 여부

원칙:
- hard fail은 항상 reject다.
- 최종 target과 time budget을 만족하면 success다.
- sigma가 유효하면 `best_cer - candidate_cer >= 2 * sigma`일 때 keep한다.
- sigma가 provisional이거나 0이면 절대 개선폭 fallback을 쓴다. 초기 권장값은 `0.01`.
- keep/reject 판단은 자기 보고나 추측이 아니라 산출물 수치로만 한다.

---

## 7. 기록 정책

Iteration 산출물:
- `runs/<hyp_id>/score_report.json`
- `runs/<hyp_id>/per_file.jsonl`
- `runs/<hyp_id>/diagnosis_report.json`
- `runs/<hyp_id>/_telemetry/` (있을 때)
- `runs/<hyp_id>/prompt.md`
- `runs/<hyp_id>/claude_stdout.txt`
- `runs/<hyp_id>/claude_stderr.txt`
- `runs/<hyp_id>/candidate.diff`

누적 기록:
- `runs/_summary/HISTORY.md`
- `runs/_summary/<job_id>_state.json`

`HISTORY.md`는 실험 로그 정본이다. 각 iteration은 최소한 다음 정보를 남긴다.

```text
iter, commit or candidate id, corpus_cer, delta, status
관찰: 주요 metric 변화
분석: 왜 그렇게 나왔다고 보는지
다음 후보: 다음에 시도할 lever
```

사후 종합 리포트는 `scripts/analyze_run.py`가 `runs/_summary/REPORT.md`로 생성한다.

---

## 8. Holdout 평가

Holdout은 Phase 3 잡이 끝난 뒤 사람이 1회만 평가한다.

1. `runs/_summary/JOB_DONE.lock` 생성
2. `scripts/analyze_run.py`로 eval 분석
3. `scripts/evaluate_holdout.py --unseal`로 holdout 평가
4. `runs/_summary/HOLDOUT.md`와 `HOLDOUT.json` 확인
5. holdout 재봉인

잡 도중 holdout 접근 또는 chmod 우회는 무효다.

---

## 9. Anti-pattern

- 계획 문서에 DoD 체크박스와 진행 로그를 섞기
- `scripts/`에 controller 상태 전이 로직을 계속 추가하기
- `judge/` 점수 산출과 `harness/` 채택 판정을 한 모듈에 섞기
- `baseline/target_cer.json`을 target 도달 전에 갱신하기
- holdout을 Phase 3 도중 읽기
- autoresearch의 Scope나 외부 hook을 운영 통제의 정본으로 삼기
- quality budget을 결과에 맞춰 사후 조정하기
