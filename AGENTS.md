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

> **본 권한 표는 Phase 3 (autoresearch 잡 실행 중) 기준**.
>
> Phase 1·2 (셋업 / 평가 인프라 구축) 에서는 사람 또는 사람이 명시 지시한
> 에이전트가 `judge/`, `frozen/`, `scripts/`, `docs/`, `tests/`, `workspace/`,
> `assets/` 를 자유롭게 작성·수정한다. Phase 3 진입 시점 ([`docs/PHASE3-PLAN.md §1`](docs/PHASE3-PLAN.md))
> 에 본 표의 제한이 *일괄 활성화* 된다.

| 영역 | Phase 3 권한 |
|------|------|
| `workspace/transcribe.py` | **편집 OK — 유일한 표면** |
| `frozen/` | **편집 금지** — backend(CT2 + whisper-large-v3-turbo) 봉인. decoding params 는 workspace 에서 자유 |
| `judge/` | **편집 금지** — 평가자 본문 |
| `baseline/` | **편집 금지** — 봉인됨. 재측정 금지 |
| `assets/audio_profile/` | **편집 금지** — 0715 audio-only profile 봉인. 원본은 `judge/verify/analyze` 만 읽고, `workspace/transcribe.py` 의 직접 경로/open 참조는 금지. 에이전트에는 `runs/<hyp_id>/diagnosis_report.json` 의 11파일 summary 와 focus 표시만 노출. 0813 은 Phase 3 *전* 생성 X |
| `scripts/` | **편집 금지** — verify / measure / analyze / evaluate_holdout 보호 |
| `docs/` | **편집 금지** — 정본·운영 문서 |
| `tests/` | **편집 금지** |
| `data/raw/.../AIG_녹취반출_20250715/` | **읽기만** — eval 데이터셋 |
| `data/raw/.../AIG_녹취반출_20250813/` | **접근 절대 금지** — holdout (chmod 000) |
| `runs/` | iteration 산출물. 읽기만 (verify 가 `score_report.json`, `per_file.jsonl`, `diagnosis_report.json`, `_telemetry/` 작성) |

holdout 이름·경로 참조 금지 범위는 `workspace/`, `judge/`, prompt, 운영 wrapper 를
제외한 `scripts/`. 운영 wrapper 예외: `scripts/seal_holdout.sh`,
`scripts/evaluate_holdout.py` (Phase 2 산출 — 잡 종료 후 1 회 평가에 holdout
batch 참조 필수). 정본·운영 문서 (`docs/`, `README.md`, `AGENTS.md`, `CLAUDE.md`)
도 명시적 안내를 위해 예외.

---

## 2. 의사결정 기준

| 무엇 | 어디 |
|------|------|
| Primary metric | `corpus_cer` (lower is better) |
| Final target | `baseline/target_cer.json` 의 `target_cer` 이하 (= 0.10, 사람이 정한 수동 목표) |
| Comparator | `baseline/target_cer.json` 의 `baseline_cer` (faster-whisper, 참조 앵커) |
| Final time target | `baseline/target_cer.json` 의 `total_inference_time_s` budget |
| 의미 있는 개선 | `Δcer ≥ 2σ` (σ = `baseline/noise_floor.json`) |
| 가드 (Phase 3) | backend/profile 직접참조·실행 실패·산술 불일치는 hard-fail. hallucination/length/repetition/coverage 는 `guard_baseline` 대비 quality budget 으로 판단 |

채택/롤백 판단의 근거는 항상 `runs/<hyp_id>/score_report.json`. 원인 추론은
`runs/<hyp_id>/diagnosis_report.json` 과 per-file 산출물만 사용한다. 추측/자기 보고 금지.

---

## 3. 정보 출처 (읽는 순서)

1. [`docs/STT-PIPELINE-SPEC.md`](docs/STT-PIPELINE-SPEC.md) — 도메인 명세 (정본)
2. [`docs/DESIGN.md`](docs/DESIGN.md) — 시스템 설계
3. [`docs/PHASE1-PLAN.md`](docs/PHASE1-PLAN.md) — Harness 구축
4. [`docs/PHASE2-PLAN.md`](docs/PHASE2-PLAN.md) — 평가 인프라 구축
5. [`docs/PHASE3-PLAN.md`](docs/PHASE3-PLAN.md) — autoresearch 실행 + 분석
6. [`README.md`](README.md) — 사람용 진입점
7. [`docs/SELF-EVOLVE-HARNESS-SPEC.md`](docs/SELF-EVOLVE-HARNESS-SPEC.md) — 참고용. 정본 승격 X

명세 본문의 라벨 문장을 prompt/후처리에 직접 주입 금지 (SPEC §11).

---

## 4. 절대 금지

[`docs/STT-PIPELINE-SPEC.md §11`](docs/STT-PIPELINE-SPEC.md) 참조 — 그대로 적용.

---

## 5. Phase 별 행동 규약

- **Phase 1** — Harness 구축 (사람 주도, 가드레일 OFF). 절차: [`docs/PHASE1-PLAN.md`](docs/PHASE1-PLAN.md). 에이전트는 지시 받은 부분 보조만.
- **Phase 2** — 평가 인프라 구축 (사람 주도, 가드레일 OFF). 절차: [`docs/PHASE2-PLAN.md`](docs/PHASE2-PLAN.md). `analyze_run.py` / `evaluate_holdout.py` / REPORT 템플릿.
- **Phase 3** — autoresearch 실행 + 분석 (에이전트 자동, 가드레일 ON). 절차: [`docs/PHASE3-PLAN.md`](docs/PHASE3-PLAN.md). `workspace/transcribe.py` 만 수정.

---

## 6. 검증 흐름

`bash scripts/verify.sh` → 마지막 줄에 `corpus_cer` 한 숫자. Phase 3 에서는 hard-fail
위반 시 exit 1 → ROLLBACK. 자세히는 [`PHASE3-PLAN.md §1.2`](docs/PHASE3-PLAN.md).

---

## 7. Commit / 브랜치

Phase 3 commit/revert 는 autoresearch 가 처리. 에이전트가 명시적으로 git 호출 X.
Phase 1·2 에서 사람 정상 커밋만.

---

## 8. 한 줄 요약

> **`workspace/transcribe.py` 한 파일만 만진다. 결정은 `score_report.json` 의 `corpus_cer`, 원인 추론은 `diagnosis_report.json`. 명세는 `docs/STT-PIPELINE-SPEC.md`.**
