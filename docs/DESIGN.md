# AIG STT — 설계 문서

> **목적**: `STT-PIPELINE-SPEC.md` 의 문제를 `uditgoenka/autoresearch` 로 풀어
> faster-whisper baseline에 견주는 corpus_cer을 자동 진화로 달성한다.
>
> 본 문서는 도메인 명세(문제 정의)는 다루지 않는다 — *어떻게 구축하고 어떻게
> 돌릴지* 만 다룬다.
> `SELF-EVOLVE-HARNESS-SPEC.md` 는 참고용 일반 원리이며, 이 저장소의 정본 규칙은
> `STT-PIPELINE-SPEC.md` 와 본 문서에 둔다.

---

## 0. 큰 그림 — 3 단계 분리

| 단계 | 무엇 | 가드레일 | 누가 |
|------|------|----------|------|
| **Phase 1 — Harness 구축** | 골격·환경·judge·baseline·σ 측정 | **OFF** (자유롭게 수정) | 사람 |
| **Phase 2 — 평가 인프라** | `analyze_run.py`·`evaluate_holdout.py`·REPORT 템플릿 | OFF | 사람 |
| **Phase 3 — Autoresearch 실행 + 분석** | autoresearch 가 transcribe.py 진화 + 잡 종료 후 Phase 2 도구로 평가 | **ON** (holdout chmod + guard hard-fail) | 에이전트 + 사람(분석) |

**왜 분리**:
- Phase 1 가드레일 켜면 셋업 자체가 막힘 (judge 작성 중 holdout 접근, 초기 스텁이 가드 위반).
- 평가 인프라(Phase 2) 를 잡 *전* 에 만들지 않으면, 잡 후 관측한 결과에 분석을 reverse-fit 할 위험.
- Phase 3 진입 시점에 가드레일 *일괄 활성화* 후 에이전트에 넘긴다.

---

## 1. 디렉토리 레이아웃

```
aig/
├── docs/
│   ├── STT-PIPELINE-SPEC.md     # 도메인 명세 (변경 금지)
│   ├── DESIGN.md                # 본 문서
│   ├── PHASE1-PLAN.md           # Harness 구축 절차
│   ├── PHASE2-PLAN.md           # 평가 인프라 구축 절차
│   ├── PHASE3-PLAN.md           # autoresearch 실행 + 분석 절차
│   ├── templates/REPORT.md      # Phase 3 보고 양식 (Phase 2 에서 생성)
│   └── SELF-EVOLVE-HARNESS-SPEC.md # 참고용 일반 하네스 원리
├── data/
│   └── raw/
│       ├── wav/AIG_녹취반출_20250715/*_l.wav
│       ├── label/AIG_녹취반출_20250715/*_l.txt
│       ├── wav/AIG_녹취반출_20250813/   ← holdout (Phase 3 에서 chmod)
│       └── label/AIG_녹취반출_20250813/
├── .cache/
│   └── ct2_models/whisper-large-v3-turbo/   # CT2 변환 캐시
├── workspace/
│   └── transcribe.py            # ← autoresearch 가 만질 유일한 파일
├── frozen/
│   └── asr_backend.py           # CT2 + whisper-large-v3-turbo 봉인 (Phase 3 편집 금지)
├── judge/
│   ├── __init__.py
│   ├── normalize.py             # §5.1 정규화
│   ├── metrics.py               # corpus_cer + edit ops + guards
│   ├── pairing.py               # label-driven _l 페어링
│   └── evaluate.py              # entry point: run → score_report.json
├── scripts/
│   ├── verify.sh                # autoresearch Verify 명령 (Phase 3 진입 시 가드 추가)
│   ├── measure_baseline.py      # faster-whisper 1회 측정
│   ├── measure_sigma.py         # 동일 스텁 3회 반복 → σ
│   ├── seal_holdout.sh          # Phase 3 진입 시 holdout chmod
│   ├── analyze_run.py           # Phase 2 산출 — runs/ 종합 분석 → REPORT.md
│   └── evaluate_holdout.py      # Phase 2 산출 — holdout 1 회 평가 + overfit 진단
├── baseline/
│   ├── target_cer.json          # 봉인된 oracle (Phase 1 종료 시 생성)
│   └── noise_floor.json         # σ (Phase 1 종료 시 생성)
├── runs/
│   ├── <hyp_id>/                # autoresearch iteration별 산출물
│   │   ├── score_report.json
│   │   ├── per_file.jsonl
│   │   └── _telemetry/*.jsonl   # optional pipeline sidecar
│   └── _summary/                # Phase 3 종료 시 산출 (REPORT.md, HOLDOUT.md)
├── tests/
├── pyproject.toml or requirements.txt
└── README.md
```

---

## 2. Phase 1 — Harness 구축

### 2.1 환경

| 항목 | 값 |
|------|------|
| Python | 3.10+ |
| Runtime | CUDA + float16 (로컬 GPU) |
| 주요 의존성 | `ctranslate2`, `faster-whisper` (baseline 측정용), `transformers`, `librosa`, `soundfile`, `numpy`, `rapidfuzz`, `huggingface_hub`, `pytest` |
| 모델 변환 | HuggingFace `openai/whisper-large-v3-turbo` → CT2 (첫 실행 시 자동, `.cache/ct2_models/` 캐시) |

### 2.2 데이터 페어링 (`judge/pairing.py`)

`STT-PIPELINE-SPEC.md §3.4` 그대로:

1. `data/raw/label/<batch>/*.txt` iterate
2. `*_l.txt` 만 통과
3. 같은 basename 의 `data/raw/wav/<batch>/<base>.wav` 페어링
4. wav 없는 label → skip + 경고
5. 환경변수 `ASR_RAW_DATA_ROOT` 로 루트 오버라이드 가능 (기본 `data/raw`)

Phase 1 에서 `AIG_녹취반출_20250715` 만 사용. holdout 배치명은 `workspace/`, `judge/`,
prompt 에서 *언급하지 않음* (Phase 3 에서 chmod 로 강제하기 전이라도 실수로 건드리지
않게).

### 2.3 정규화 (`judge/normalize.py`)

`STT-PIPELINE-SPEC.md §5.1` 의 문자열 변환 1~6 단계를 구현:

1. Unicode NFC
2. `[INAUDIBLE]` 제거 (case-insensitive)
3. 구두점 제거 (명세에 명시된 문자열 그대로)
4. 영문 lowercase
5. 숫자 그대로
6. whitespace 전부 제거

> hypothesis 와 reference 에 **동일 함수** 적용. 분기 금지. character-level Levenshtein
> distance 와 editops 는 `judge/metrics.py` 에서 산출한다.

### 2.4 메트릭 (`judge/metrics.py`)

| 메트릭 | 산출 |
|--------|------|
| `corpus_cer` | `Σ edits / Σ ref_chars` (primary) |
| `macro_cer` | per-file CER 산술 평균 (진단) |
| `sub / del / ins` | edit-op 분해 (per-file + corpus 합) |
| `hallucination_hit_rate` | §6.4 패턴 검출 — **정규화 전 원본 hyp 에 대해** |
| `empty_output_rate` | hyp 빈 문자열 비율 |
| `length_ratio` (mean/p05/p95) | hyp_chars / ref_chars |
| `repeated_text_rate` | 반복 문장 검출 |
| `audio_coverage_rate` | optional sidecar 가 있을 때 처리 audio_s / 전체 audio_s |
| `total_inference_time_s` | wall clock 합 |
| `runtime_s_per_audio_min` | 단위 시간당 처리 시간 |

**Phase 1 에서는 *전부 산출만* 한다 — hard-fail 임계 없음.** coverage telemetry 가 없으면
`audio_coverage_rate` 는 `null` 또는 omit 하고 warning 만 남긴다.

### 2.5 Judge entry (`judge/evaluate.py`)

```
python -m judge.evaluate \
    --batch AIG_녹취반출_20250715 \
    --transcribe workspace.transcribe:transcribe \
    --out runs/<hyp_id>/score_report.json
```

동작:
1. `pairing` 으로 12 페어 로드
2. 각 wav → `librosa.load(sr=16000, mono=True)` → telemetry env 설정 → `transcribe(audio, sr)` 호출
3. per-file CER + 가드 산출 → `score_report.json` 작성
4. **마지막 줄에 `corpus_cer` 한 숫자 print** (autoresearch Verify 가 파싱)

`transcribe(audio, sr) -> str` 계약은 유지한다. pipeline 이 coverage 를 보고하고 싶으면
judge 가 설정한 `ASR_TELEMETRY_DIR`, `ASR_TELEMETRY_FILE_ID` 를 사용해
`runs/<hyp_id>/_telemetry/<file_id>.jsonl` sidecar 를 쓴다. judge 는 있으면 읽고, 없으면
coverage guard 를 skip 한다.

### 2.6 Frozen layer (`frozen/asr_backend.py`)

backend 봉인. workspace 는 `load()` 와 `generate()` 만 호출하며, 모델·디바이스·precision
은 이 layer 가 hard-code. Phase 3 진입 시 편집 금지.

```python
# frozen/asr_backend.py
def load() -> tuple[Whisper, WhisperProcessor]: ...     # 모델 캐시·고정 로드
def generate(features, prompts, **decoding_kwargs): ... # CT2 generate passthrough
```

- 봉인: 모델 이름, 변환 캐시 경로, device, compute_type
- workspace 자유: decoding_kwargs (beam, temperature, fallback, sampling 등 전부)
- 추가 보호: Phase 3 verify 가 workspace 의 `import ctranslate2.models` / `from_pretrained` 패턴 정적 검사 (PHASE3 §1.2)

### 2.7 초기 transcribe 스텁 (`workspace/transcribe.py`)

가장 단순한 호출 — `frozen.load()` + `frozen.generate()` 로 chunking 없이 1 회.
30 초 초과 long-form 은 깨질 거고, **그게 autoresearch 가 풀어야 할 출발점**.

```python
from frozen.asr_backend import load, generate

def transcribe(audio: np.ndarray, sr: int) -> str:
    model, proc = load()
    # feature 추출 → prompt 구성 → generate (단일 호출, chunking 없음) → decode → str
    ...
```

스텁 작성 기준: *돌아가기만* 하면 됨. CER 점수는 나쁠 거고, 그래야 autoresearch
가 개선 여지를 갖는다.

### 2.8 Verify 스크립트 (`scripts/verify.sh`)

**Phase 1 에서는 가드 검사 없음**:

```bash
#!/usr/bin/env bash
set -euo pipefail
HYP_ID="${HYP_ID:-manual_$(date +%s)}"
python -m judge.evaluate \
    --batch AIG_녹취반출_20250715 \
    --transcribe workspace.transcribe:transcribe \
    --out "runs/${HYP_ID}/score_report.json"
# 마지막에 corpus_cer 만 한 줄 print (judge 가 이미 함)
```

### 2.9 Baseline 측정 (`scripts/measure_baseline.py`)

`faster-whisper` 로 0715 12 페어 1회 transcribe → 동일 judge 로 점수 산출 → `baseline/target_cer.json` 작성:

```json
{
  "target_cer": 0.0XXX,
  "macro_cer": 0.0XXX,
  "num_files": 12,
  "batches": ["AIG_녹취반출_20250715"],
  "total_audio_s": ...,
  "total_inference_time_s": ...,
  "versions": {
    "ctranslate2": "...",
    "faster_whisper": "...",
    "transformers": "..."
  },
  "model": {
    "name": "openai/whisper-large-v3-turbo",
    "revision": "<hf-sha>",
    "quantization": "float16"
  },
  "decoding_params": {...},
  "hardware": {"gpu": "...", "compute_type": "float16"},
  "per_file": [...]
}
```

이후 **재실행 금지** (결정론 보장). 한 번 봉인되면 모든 의사결정의 기준점.

### 2.10 σ 측정 (`scripts/measure_sigma.py`)

`workspace/transcribe.py` 초기 스텁으로 0715 평가를 **3회 반복** → corpus_cer 분포의
표준편차 → `baseline/noise_floor.json`:

```json
{
  "sigma": 0.00XX,
  "samples": [0.XXX, 0.XXX, 0.XXX],
  "measured_against": "initial transcribe stub",
  "measured_at": "2026-05-28T..."
}
```

> 주의: 스텁이 *돌긴 돌아야* σ 측정 가능. 스텁이 너무 망가져 corpus_cer 산출 자체가
> 깨지면 σ 측정은 첫 정상 가설 이후로 미룬다.

### 2.11 Phase 1 완료 기준 (Definition of Done)

- [ ] 데이터 페어링 코드가 0715 12 페어 정확히 매칭
- [ ] judge 가 스텁 transcribe 에 대해 score_report.json 산출
- [ ] `scripts/verify.sh` 실행 시 corpus_cer 숫자가 마지막 줄에 출력
- [ ] `baseline/target_cer.json` 생성 + 봉인 (재실행 금지 명시)
- [ ] `baseline/target_cer.json` 에 versions/model/decoding_params/hardware 기록
- [ ] `baseline/noise_floor.json` 생성 (σ 측정 완료)
- [ ] normalize/pairing/metrics/evaluate smoke 테스트 통과
- [ ] 사람이 수동으로 verify.sh 1 회 돌려서 cer 숫자 확인
- [ ] holdout 배치명은 `workspace/`, `judge/`, prompt, 운영 wrapper 를 제외한 `scripts/` 에서
      참조하지 않음 (docs/README/AGENTS/CLAUDE 와 `scripts/seal_holdout.sh` 는 예외)

---

## 3. Phase 2 — 평가 인프라

상세는 [`PHASE2-PLAN.md`](PHASE2-PLAN.md). 정본으로 둔다.

요점만:
- 진입 시점: Phase 1 DoD 통과 직후, autoresearch 잡 *전*
- 산출물: `scripts/analyze_run.py`, `scripts/evaluate_holdout.py`, `docs/templates/REPORT.md`
- 평가 8 개 축 (A 결과 / B 하네스 구멍 / C 에이전트 시야 / D 탐색 다양성 / E 메트릭 적절성 / F 비용 / G Attribution / H Reasoning 품질)
- 합성 데이터로 smoke test — 실제 잡 결과 없이 분석 도구 검증

> 잡 끝나고 분석 도구 만들면 *관측한 결과에 분석을 reverse-fit* 할 위험.

---

## 4. Phase 3 — Autoresearch 실행 + 분석

상세는 [`PHASE3-PLAN.md`](PHASE3-PLAN.md). 정본으로 둔다.

요점만:
- 진입 시점: Phase 1 + Phase 2 DoD 통과 직후
- 진입 직전 일괄 활성화: holdout chmod, verify 가드 hard-fail
- `workspace/transcribe.py` 만 scope, `corpus_cer` 만 metric
- 결정 = autoresearch (keep/revert), 가드 위반 = 점수 무관 즉시 ROLLBACK
- 잡 종료 후 Phase 2 도구로 평가 — REPORT.md + HOLDOUT.md

---

## 5. 열려 있는 질문 (셋업 시 확인)

- GPU 사양·메모리: large-v3-turbo float16 + faster-whisper 동시 적재 가능한지
- Python 버전, ctranslate2 버전 호환성
- 데이터 실제 절대경로 — `ASR_RAW_DATA_ROOT` 설정 여부
- autoresearch 의 노이즈 σ 임계 지원 여부 (PHASE3 §1.2)
- autoresearch 의 파일 접근 권한 / 디렉토리 제한 메커니즘 (PHASE3 §3)
- 25 iter 총 소요 시간 추정 (per-iter verify 시간 측정 후)

---

## 6. 안티 패턴 (해선 안 되는 것)

- Phase 1 단계에서 가드레일을 미리 활성화 (코드 작성 막힘)
- Phase 3 에서 가드 임계를 baseline 측정 *전에* 정함 (실측 분포 없이 임계 못 정함)
- 평가 도구를 Phase 3 *후* 에 만듦 (결과에 분석을 맞추는 reverse-fit)
- baseline/target_cer.json 을 잡 도중 갱신 (결정론 깨짐)
- σ 를 단일 측정으로 산출 (최소 3회)
- holdout 을 `workspace/`, `judge/`, prompt 에서 *언급* (실수로 참조 가능)
- judge 와 transcribe 가 같은 정규화 모듈을 import 하지 않고 각자 구현
- 8 개 축을 단일 점수로 환원해 자동 판정 — 사람 판단 항목은 사람이 채움
