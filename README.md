# AIG STT

한국어 보험 콜센터 통화에 대해 `ctranslate2 + whisper-large-v3-turbo` 추론 파이프라인을
자동 진화시켜, `corpus-level CER` 을 faster-whisper baseline 이하로 낮추는 실험.

루프 엔진은 [`uditgoenka/autoresearch`](https://github.com/uditgoenka/autoresearch).
도메인 정의·정규화·가드의 정본은 `docs/STT-PIPELINE-SPEC.md` 에 동결.

---

## 디렉토리

```
aig/
├── docs/
│   ├── STT-PIPELINE-SPEC.md      # 도메인 명세 (source of truth)
│   ├── DESIGN.md                 # 시스템 설계
│   ├── PHASE1-PLAN.md            # Harness 구축 절차
│   ├── SELF-EVOLVE-HARNESS-SPEC.md # 참고용 일반 하네스 원리
│   └── PHASE2-PLAN.md            # autoresearch 자동화 절차 (예정)
├── workspace/transcribe.py       # 에이전트가 진화시키는 유일한 파일
├── judge/                        # 평가자 (정규화 + corpus_cer + 가드)
├── scripts/                      # verify / baseline / σ 측정 / holdout seal
├── baseline/                     # target_cer + noise_floor (봉인)
├── data/raw/                     # wav + label
├── runs/                         # iteration 별 score_report + per_file
├── AGENTS.md                     # 에이전트 공통 규약
└── CLAUDE.md                     # Claude Code 보충
```

---

## 두 단계 구조

| 단계 | 무엇 | 가드레일 | 누가 |
|------|------|----------|------|
| **Phase 1** | 골격·환경·judge·baseline·σ 구축 | OFF | 사람 |
| **Phase 2** | autoresearch 가 transcribe.py 진화 | ON | 에이전트 |

자세히는 `docs/DESIGN.md`.

---

## 빠른 시작

### 사전

- Linux + CUDA + float16 가능한 GPU (~14GB+)
- Python 3.10+
- `data/raw/wav/AIG_녹취반출_20250715/*_l.wav` 14개 배치 완료
- `data/raw/label/AIG_녹취반출_20250715/*_l.txt` 14개 배치 완료
- (선택) `ASR_RAW_DATA_ROOT` 로 데이터 루트 오버라이드 가능

### 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt    # 또는 pyproject.toml
# 첫 실행 시 .cache/ct2_models/ 에 CT2 변환 캐시 자동 생성
```

### Phase 1 — 한 번만 수행

```bash
# 1. 수동 verify (스텁이 도는지 확인)
bash scripts/verify.sh

# 2. faster-whisper baseline 봉인
python scripts/measure_baseline.py

# 3. 노이즈 σ 측정
python scripts/measure_sigma.py
```

Phase 1 DoD 는 `docs/PHASE1-PLAN.md §8` 체크리스트 참조.

### Phase 2 — autoresearch 루프

Claude Code 세션에서:

```
/autoresearch
Goal: workspace/transcribe.py 의 transcribe(audio, sr) 를 진화시켜 0715 14 페어 corpus_cer 을 baseline/target_cer.json 의 target_cer 이하로 낮춘다. backend·model 변경 금지.
Scope: workspace/transcribe.py
Metric: corpus_cer (lower is better)
Verify: bash scripts/verify.sh
Iterations: 25
```

진입 직전에 한 번:

```bash
bash scripts/seal_holdout.sh        # 0813 디렉토리 chmod 000
```

종료 후 holdout 복구:

```bash
chmod -R u+rwX data/raw/wav/AIG_녹취반출_20250813
chmod -R u+rwX data/raw/label/AIG_녹취반출_20250813
```

---

## 절대 금지

- `data/raw/.../AIG_녹취반출_20250813` (holdout) 접근
- 모델·backend 변경 (whisper-large-v3-turbo + ctranslate2 고정)
- 모델 fine-tuning
- 평가 label 본문을 prompt 에 직접 주입
- `baseline/*.json` 재측정 / 덮어쓰기

(전체 목록: `docs/STT-PIPELINE-SPEC.md §11`)

---

## 산출물 보는 법

각 iteration:
- `runs/<hyp_id>/score_report.json` — 메트릭 + 가드
- `runs/<hyp_id>/per_file.jsonl` — per-file 진단 telemetry
- `runs/<hyp_id>/_telemetry/*.jsonl` — 선택적 pipeline sidecar telemetry
- `git log` — autoresearch 가 만든 commit 이력 (keep) 또는 revert 흔적

성공 = `runs/<hyp_id>/score_report.json` 의 `corpus_cer ≤ target_cer`.

---

## 문서 색인

| 문서 | 용도 |
|------|------|
| `docs/STT-PIPELINE-SPEC.md` | 문제 정의·정규화·가드 (불변) |
| `docs/DESIGN.md` | 시스템 설계 (2 단계 구조) |
| `docs/PHASE1-PLAN.md` | Harness 구축 단계별 |
| `docs/SELF-EVOLVE-HARNESS-SPEC.md` | 참고용 일반 하네스 원리 (정본 아님) |
| `docs/PHASE2-PLAN.md` | autoresearch 운영 (예정) |
| `AGENTS.md` | 에이전트 공통 규약 |
| `CLAUDE.md` | Claude Code 보충 |
