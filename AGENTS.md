# AGENTS.md

이 저장소에서 작업하는 모든 AI 에이전트가 따라야 할 규약. 사람·LLM 도구 무관.

---

## 0. 한 줄 임무

한국어 보험 콜센터 음성에 대해, `ctranslate2 + whisper-large-v3-turbo` 위에
`transcribe(audio, sr) -> str` 파이프라인을 자동 진화시켜 corpus-level CER 을
사람이 정한 `target_cer` (현재 0.10) 이하로 낮춘다. `total_inference_time_s` 는
baseline time budget 안에 들어야 한다. faster-whisper `baseline_cer` 은 거리감
앵커일 뿐 성공 기준은 아니다 (§2 표 / `STT-PIPELINE-SPEC.md §7`).

---

## 1. 무엇을 만지고 무엇을 만지지 않는가

> **본 권한 표는 Phase 3 (자체 harness 잡 실행 중) 기준**.
>
> Phase 1·2 (셋업 / 평가 인프라 구축) 에서는 사람 또는 사람이 명시 지시한
> 에이전트가 `judge/`, `frozen/`, `scripts/`, `docs/`, `tests/`, `workspace/`,
> `assets/` 를 자유롭게 작성·수정한다. Phase 3 진입 시점 ([`docs/PHASE3-PLAN.md §3`](docs/PHASE3-PLAN.md))
> 에 본 표의 제한이 *일괄 활성화* 된다.

| 영역 | Phase 3 권한 |
|------|------|
| `workspace/transcribe.py` | **편집 OK — 유일한 표면** |
| `frozen/` | **편집 금지** — backend(CT2 + whisper-large-v3-turbo) 봉인. decoding params 는 workspace 에서 자유 |
| `judge/` | **편집 금지** — 평가자 본문 |
| `baseline/` | **편집 금지** — 봉인됨. 재측정 금지 |
| `assets/audio_profile/` | **편집 금지** — 0715 audio-only profile 봉인. 원본은 `judge/verify/analyze` 만 읽고, `workspace/transcribe.py` 의 직접 경로/open 참조는 금지. 에이전트에는 `runs/<hyp_id>/diagnosis_report.json` 의 11파일 summary 와 focus 표시만 노출. 0813 은 Phase 3 *전* 생성 X |
| `harness/` | **편집 금지** — Phase 3 controller 본체. guard/policy/state/history/runner 보호 |
| `scripts/` | **편집 금지** — 사람이 실행하는 thin CLI / 운영 명령 보호 |
| `.claude/` | **편집 금지** — legacy guard 자산. 정리 전까지 보호 |
| `.ckignore` | **편집 금지** — legacy context 차단 패턴. 정리 전까지 보호 |
| `docs/` | **편집 금지** — 정본·운영 문서 |
| `tests/` | **편집 금지** |
| `data/raw/.../AIG_녹취반출_20250715/` | **읽기만** — eval 데이터셋 |
| `data/raw/.../AIG_녹취반출_20250813/` | **접근 절대 금지** — holdout (chmod 000) |
| `runs/<hyp_id>/` | iteration 산출물. 읽기만 (verify 가 `score_report.json`, `per_file.jsonl`, `diagnosis_report.json`, `_telemetry/` 작성) |
| `runs/_summary/` | **편집 금지** — harness 전용 HISTORY/state/REPORT 영역. 후보가 만들거나 덮어쓰면 scope 위반 |

holdout 이름·경로 참조 금지의 핵심은 **read access** 다. 다음 카테고리는 예외:
- 운영 wrapper (잡 종료 후 1 회 평가 시 holdout batch 식별 필수):
  `scripts/seal_holdout.sh`, `scripts/evaluate_holdout.py`
- defensive deny 가드 (오타로 holdout 측정 방지 목적의 hard-coded `_FORBIDDEN_BATCHES`):
  `scripts/measure_baseline.py`, `scripts/build_audio_profile.py`
- 회귀 테스트 fixture / hook deny 패턴 검증: `tests/`, `.claude/hooks/`,
  `.claude/settings.json`
- 정본·운영 문서: `docs/`, `README.md`, `AGENTS.md`, `CLAUDE.md`

위 외 (`workspace/`, `judge/`, prompt, `harness/` 본문, 그 외 `scripts/`) 에서는
이름 참조도 금지.

---

## 2. 의사결정 기준

| 무엇 | 어디 |
|------|------|
| Primary metric | `corpus_cer` (lower is better) |
| Final target | `baseline/target_cer.json` 의 `target_cer` 이하 (= 0.10, 사람이 정한 수동 목표) |
| Comparator | `baseline/target_cer.json` 의 `baseline_cer` (faster-whisper, 참조 앵커) |
| Final time target | `baseline/target_cer.json` 의 `total_inference_time_s` budget |
| 의미 있는 개선 | `Δcer ≥ 2σ` (σ = `baseline/noise_floor.json`) |
| 가드 (Phase 3) | `harness/guards.py` 기준. backend/profile 직접참조·실행 실패·산술 불일치는 hard-fail. hallucination/length/repetition/coverage 는 `guard_baseline` 대비 quality budget 으로 판단 |

채택/롤백 판단의 근거는 항상 `runs/<hyp_id>/score_report.json`. 원인 추론은
`runs/<hyp_id>/diagnosis_report.json` 과 per-file 산출물만 사용한다. 추측/자기 보고 금지.

---

## 3. 정보 출처 (읽는 순서)

1. [`docs/SSOT.md`](docs/SSOT.md) — 문서별 정본 지도
2. [`docs/STT-PIPELINE-SPEC.md`](docs/STT-PIPELINE-SPEC.md) — 도메인 명세 (정본)
3. [`docs/DESIGN.md`](docs/DESIGN.md) — 시스템 설계
4. [`docs/PHASE1-PLAN.md`](docs/PHASE1-PLAN.md) — Harness 구축
5. [`docs/PHASE2-PLAN.md`](docs/PHASE2-PLAN.md) — 평가 인프라 구축
6. [`docs/PHASE3-PLAN.md`](docs/PHASE3-PLAN.md) — 자체 harness 실행 + 분석
7. [`docs/PHASE3-STATUS.md`](docs/PHASE3-STATUS.md) — Phase 3 DoD 체크 상태
8. [`README.md`](README.md) — 사람용 진입점
9. [`docs/SELF-EVOLVE-HARNESS-SPEC.md`](docs/SELF-EVOLVE-HARNESS-SPEC.md) — 참고용. 정본 승격 X

명세 본문의 라벨 문장을 prompt/후처리에 직접 주입 금지 (SPEC §11).

---

## 4. 절대 금지

[`docs/STT-PIPELINE-SPEC.md §11`](docs/STT-PIPELINE-SPEC.md) 참조 — 그대로 적용.

---

## 5. Phase 별 행동 규약

- **Phase 1** — Harness 구축 (사람 주도, 가드레일 OFF). 절차: [`docs/PHASE1-PLAN.md`](docs/PHASE1-PLAN.md). 에이전트는 지시 받은 부분 보조만.
- **Phase 2** — 평가 인프라 구축 (사람 주도, 가드레일 OFF). 절차: [`docs/PHASE2-PLAN.md`](docs/PHASE2-PLAN.md). `analyze_run.py` / `evaluate_holdout.py` / REPORT 템플릿.
- **Phase 3** — 자체 harness 실행 + 분석 (controller 자동, 가드레일 ON). 절차: [`docs/PHASE3-PLAN.md`](docs/PHASE3-PLAN.md). 후보 표면은 `workspace/transcribe.py` 만 수정.

---

## 6. 검증 흐름

`bash scripts/verify.sh` → 마지막 줄에 `corpus_cer` 한 숫자. Phase 3 에서는
`harness/guards.py` 기준 hard-fail 위반 시 exit 1 → reject/rollback. 자세히는
[`PHASE3-PLAN.md`](docs/PHASE3-PLAN.md).

`scripts/verify.sh` 는 사람이 직접 실행할 수 있는 평가 entrypoint다. 수치 가드는
`harness/guards.py`가 담당한다. 과거 `verify.sh.alt` / swap 구조는 자체 harness
전환 과정의 정리 대상으로 [`docs/PHASE3-STATUS.md`](docs/PHASE3-STATUS.md)에서 추적한다.

`.claude/` / `.claude.alt/` / swap 스크립트는 autoresearch 운영 잔재다. 자체
harness 전환 뒤 폐기 또는 archive 여부는 [`docs/PHASE3-STATUS.md`](docs/PHASE3-STATUS.md)
에서 추적한다.

---

## 7. Commit / 브랜치

Phase 3 keep/reject/rollback 은 자체 harness 정책으로 처리한다. 수동 git 조작은
작업 브랜치 상태와 `runs/_summary/HISTORY.md` 기록을 깨지 않게 제한한다.
Phase 1·2 에서 사람 정상 커밋만.

---

## 8. 한 줄 요약

> **후보 표면은 `workspace/transcribe.py` 한 파일. 결정은 `score_report.json` 의 `corpus_cer`, 정책은 `harness/`, 원인 추론은 `diagnosis_report.json`.**
