# AIG STT

한국어 보험 콜센터 통화에 대해 `ctranslate2 + whisper-large-v3-turbo` 추론 파이프라인을
자동 진화시켜 `corpus-level CER` 을 사람이 정한 목표 (`baseline/target_cer.json:target_cer`,
현재 0.10) 이하로 낮추는 실험. `faster-whisper baseline_cer` (현재 0.43) 은 동일 데이터에서의
거리감 측정용 참조 앵커일 뿐 성공 기준은 아니다.

루프 엔진: repository 내부 `harness/` controller. 기존 `autoresearch` 조사는
historical 문서로 보존한다.

정본:
- 문서 지도·SSOT — [`docs/SSOT.md`](docs/SSOT.md)
- 도메인 정의·정규화·가드 — [`docs/STT-PIPELINE-SPEC.md`](docs/STT-PIPELINE-SPEC.md)
- 시스템 설계·디렉토리·3 단계 구조 — [`docs/DESIGN.md`](docs/DESIGN.md)

---

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 첫 실행 시 .cache/ct2_models/ 에 CT2 변환 캐시 자동 생성
```

사전 조건: Linux + CUDA float16 GPU (~14GB+), Python 3.10+, 11 페어:
- `data/raw/wav/AIG_녹취반출_20250715/*_l.wav`
- `data/raw/label/AIG_녹취반출_20250715/*_l.txt`

`ASR_RAW_DATA_ROOT` 로 데이터 루트 오버라이드 가능.

---

## 진행 절차

| 단계 | 무엇 | 어디 |
|------|------|------|
| Phase 1 | Harness 구축 (사람) | [`docs/PHASE1-PLAN.md`](docs/PHASE1-PLAN.md) |
| Phase 2 | 평가 인프라 구축 (사람) | [`docs/PHASE2-PLAN.md`](docs/PHASE2-PLAN.md) |
| Phase 3 | 자체 harness 실행 + 분석 | [`docs/PHASE3-PLAN.md`](docs/PHASE3-PLAN.md) |

Phase 3 기본 실행 형태:

```bash
python3 scripts/evolve.py --job-id phase3_001 --iters 25 --candidate-cmd "claude -p" --commit-results
```

`--iters > 1` 이면 `--commit-results` 가 **필수** (직전 best 를 git 기준점으로 고정해야
reject 시 안전한 rollback 가능 — PHASE3-PLAN §4 참조).

---

## 절대 금지

요약만 — 전체 목록은 [`docs/STT-PIPELINE-SPEC.md §11`](docs/STT-PIPELINE-SPEC.md).

- holdout (`AIG_녹취반출_20250813`) 접근
- 모델·backend 변경 (whisper-large-v3-turbo + ctranslate2 고정)
- `baseline/*.json` 재측정 / 덮어쓰기

---

## 문서 색인

| 문서 | 용도 |
|------|------|
| [`docs/SSOT.md`](docs/SSOT.md) | 문서별 정본 책임 지도 |
| [`docs/STT-PIPELINE-SPEC.md`](docs/STT-PIPELINE-SPEC.md) | 문제 정의·정규화·가드 (정본) |
| [`docs/DESIGN.md`](docs/DESIGN.md) | 시스템 설계 (3 단계 구조, 디렉토리) |
| [`docs/PHASE1-PLAN.md`](docs/PHASE1-PLAN.md) | Harness 구축 단계별 |
| [`docs/PHASE2-PLAN.md`](docs/PHASE2-PLAN.md) | 평가 인프라 구축 단계별 |
| [`docs/PHASE3-PLAN.md`](docs/PHASE3-PLAN.md) | 자체 harness 실행 + 분석 절차 |
| [`docs/PHASE3-STATUS.md`](docs/PHASE3-STATUS.md) | Phase 3 DoD 체크 상태 |
| [`docs/PHASE3-LOOP.md`](docs/PHASE3-LOOP.md) | Phase 3 루프/노출/판정 구조 Mermaid |
| [`docs/AUTORESEARCH.md`](docs/AUTORESEARCH.md) | 이전 autoresearch 조사 기록 (historical) |
| [`docs/SELF-EVOLVE-HARNESS-SPEC.md`](docs/SELF-EVOLVE-HARNESS-SPEC.md) | 참고용 일반 하네스 원리 (정본 아님) |
| [`AGENTS.md`](AGENTS.md) | 에이전트 공통 규약 |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code 보충 |
