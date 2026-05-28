# Review — Phase 1 commit `be9853f`

검토 대상:
- Base: `df51a60 Phase 1: judge + frozen backend + workspace stub + audio profile`
- Head: `be9853f target_cer 와 baseline_cer 분리 + degenerate label 자동 스킵`
- 범위: Phase 1 코드/문서 변경. 코드 수정 없이 리뷰만 수행.

검증:
- `git diff --check HEAD~1..HEAD` 통과
- `python3 -m pytest` → `17 passed in 1.55s`

## 결론

Phase 1 smoke/unit 테스트는 통과한다. 다만 Phase 3 로 바로 들어가기에는 목표 정의와
telemetry 계약 쪽에 정리해야 할 문제가 있다. Phase 2/3 산출물 부재는 현재 Phase 1
작업의 직접 실패는 아니지만, Phase 3 진입 전에는 blocker 다.

## Findings

### P0 — 목표 정의가 정본/진입점 사이에서 충돌

`README.md:3-4`, `AGENTS.md:9-11` 은 여전히 "faster-whisper baseline 이하"를
프로젝트 목표로 말한다. 반면 `docs/STT-PIPELINE-SPEC.md:277-280`,
`docs/PHASE3-PLAN.md:27-35` 는 `target_cer=0.10` 을 수동 목표로 두고
`baseline_cer` 는 비교 앵커일 뿐 성공 기준이 아니라고 정의한다.

이 상태로 Phase 3 에 들어가면 autoresearch goal, verify success 조건, 사람의 기대
기준이 서로 달라진다. 먼저 둘 중 하나로 확정해야 한다.

- 선택 A: 최종 목표 = faster-whisper baseline. 그러면 `target_cer` 는 측정된
  `baseline_cer` 를 가리키거나 같은 값이어야 한다.
- 선택 B: 최종 목표 = 사람이 정한 0.10. 그러면 README/AGENTS 한 줄 임무와
  `STT-PIPELINE-SPEC.md:499` 의 "faster-whisper baseline = target 상한" 문구를
  모두 바꿔야 한다.

### P1 — telemetry coverage 키가 문서 계약과 evaluator 구현이 다름

`docs/STT-PIPELINE-SPEC.md:449-458` 은 JSONL 정본 필드를 `start_s`, `end_s` 로
정의한다. 그런데 `judge/evaluate.py:49-66` 의 `_read_coverage()` 는 `start`,
`end` 키만 읽는다.

따라서 Phase 3 파이프라인이 문서대로 `_telemetry/<file_id>.jsonl` 을 emit 해도
`audio_coverage_rate` 는 계속 `null` 이 된다. coverage 가드는 "telemetry 있을 때만"
작동한다는 설계라서, 이 불일치는 coverage 진단/가드를 사실상 비활성화한다.

수정 방향: evaluator 가 `start_s/end_s` 를 우선 읽고, 필요하면 legacy `start/end` 도
fallback 으로 받게 한다.

### P1 — Phase 2/3 필수 산출물이 아직 없음

문서상 Phase 3 종료 후 분석은 `scripts/analyze_run.py`, `scripts/evaluate_holdout.py`,
`docs/templates/REPORT.md` 를 요구한다. 현재 repo 에는 세 파일이 없다.

이는 Phase 1 커밋 자체의 실패라기보다는 아직 Phase 2 가 남아 있다는 의미다. 하지만
Phase 3 실행/종료/holdout 1회 평가까지 보려면 blocker 로 취급해야 한다.

### P1 — Phase 3 verify hard-fail wrapper 가 아직 없음

`docs/PHASE3-PLAN.md:42-52` 는 backend/profile 직접참조 차단, 산술 무결성,
runtime hard cap, quality budget 을 요구한다. 현재 `scripts/verify.sh:1-18` 은 Phase 1
용 judge 실행만 한다.

Phase 1 에서는 정상이다. 다만 Phase 3 진입 직전에는 별도 wrapper 또는 같은 스크립트의
Phase 3 모드로 이 가드들이 구현되어야 한다.

### P2 — 11개 전환 후 stale 문구가 남음

대부분 0715 평가 대상이 11 페어로 바뀌었지만, `docs/PHASE1-PLAN.md:409-413` 과
`docs/PHASE1-PLAN.md:465-471` 에 `per_file 12 행`이 남아 있다. 같은 문서 안에서
11/12가 섞여 Phase 1 DoD 판단이 헷갈린다.

### P2 — empty-reference 제외 산술은 문서에 더 명확히 반영 필요

`judge/metrics.py:129-146` 은 `ref_chars == 0` 파일을 `corpus_cer` 와 edit breakdown
합산에서 제외한다. 현재 0715 의 degenerate label 은 `pair_batch()` 에서 기본 스킵되어
실행 경로상 문제는 작다.

다만 `docs/STT-PIPELINE-SPEC.md:153-156` 의 집계 정의는 단순히
`Σ edits / Σ ref_chars` 라고만 되어 있어, "empty reference 파일은 corpus CER 산술에서
제외한다"는 정책이 정본에 분명히 박혀 있지 않다. holdout 또는 diagnostics 에서 같은
케이스가 생기면 해석 충돌 여지가 있다.

## Notes

- `scripts/seal_holdout.sh` 와 `scripts/verify.sh` 는 둘 다 executable bit 가 설정되어 있다.
- `pair_batch()` 의 degenerate label 기본 스킵과 `num_files_scored` 추가는 방향이 좋다.
- `target_cer` / `baseline_cer` 분리는 의도 자체는 가능하지만, 현재 프로젝트 목표와 충돌하므로
  Phase 3 전에 반드시 한 번 더 결정해야 한다.
