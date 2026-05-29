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

---

## Phase 3 진입 → 본 잡 → 종료

각 단계의 *의미*·*실패 모드*·*해석* 은 [`docs/PHASE3-PLAN.md §3`](docs/PHASE3-PLAN.md)
참조. 본 섹션은 copy-paste 가능한 실행 시퀀스만.

### 1. 진입 가드

```bash
# 1-1. holdout 봉인
bash scripts/seal_holdout.sh

# 1-2. 정상 verify 1회
bash scripts/verify.sh
# stub 상태에서는 length_ratio.p05 catastrophic 가드 발동이 정상 (PLAN §3.5).

# 1-3. 의도적 위반 smoke
sed -i '1i import ctranslate2' workspace/transcribe.py
bash scripts/verify.sh
# "verify FAIL [static backend]" + exit 1 확인 후 원복:
git checkout -- workspace/transcribe.py

# 1-4. 자체 harness dry-run 1 iter
python3 scripts/evolve.py --job-id dry --iters 1 --manual
# 정리 (dry artifacts 가 다음 잡의 ensure_worktree_ready 를 막음 — PLAN §3.7):
git checkout -- runs/_summary/HISTORY.md
rm -f runs/_summary/dry_state.json
rm -rf runs/dry_*
git status   # working tree clean 확인
```

### 2. 본 잡 (25 iter)

```bash
python3 scripts/evolve.py \
  --job-id phase3_001 \
  --iters 25 \
  --candidate-cmd "claude -p" \
  --commit-results
```

`--iters > 1` 이면 `--commit-results` 가 **필수** (직전 best 를 git 기준점으로 고정해야
reject 시 안전한 rollback 가능 — PLAN §4).

### 3. 종료 후 분석

```bash
touch runs/_summary/JOB_DONE.lock
python3 scripts/analyze_run.py
python3 scripts/evaluate_holdout.py --unseal
# REPORT.md, HOLDOUT.md, HOLDOUT.json 확인 후 holdout 자동 재봉인.
```

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
