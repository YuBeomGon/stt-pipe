# Phase 1 — Harness 구축 플랜

> **범위**: `DESIGN.md` 의 Phase 1 만. 가드레일은 전부 OFF 상태로 골격·judge·baseline·σ
> 까지 완성하는 것이 목표. Phase 2 (평가 인프라) / Phase 3 (autoresearch 실행 + 분석) 은 별도 플랜.

작업 순서는 **의존 관계 기반**. judge 가 모든 측정의 기반이므로 가장 먼저, 그 다음
스텁, baseline, σ 순.

---

## Step 0 — 사전 점검 (블로커 제거)

먼저 확정해야 할 것들. 한 가지라도 막히면 그 자리에서 멈추고 해소.

| 항목 | 확인 방법 | 막히면 |
|------|----------|--------|
| GPU 가용성 | `nvidia-smi` — float16 14GB 정도 여유? | CUDA 환경 정비 |
| Python 3.10+ | `python --version` | venv 따로 |
| 데이터 존재 | `ls $ASR_RAW_DATA_ROOT/wav/AIG_녹취반출_20250715/*_l.wav \| wc -l` → 12 기대 | 사용자에게 위치 확인 |
| label 매칭 | 동일 디렉토리 label 도 12개 | 사용자에게 확인 |
| holdout 존재 (참고만) | 0813 존재 확인 (13 페어 기대), **건드리지 않음** | — |
| Git 초기화 | `git rev-parse --show-toplevel` | `git init` 후 `.gitignore` 작성 |
| 인터넷 | HF model 다운로드 가능 | proxy/mirror 설정 |

**산출물**: `notes/env-check.txt` (선택) 에 GPU·Python·데이터 카운트 기록.

### 0.1 데이터 경로 config

`ASR_RAW_DATA_ROOT` env var 로 데이터 루트를 설정. 기본은 프로젝트 상대 `data/raw`.
다른 머신에서는 절대경로로 override.

`.env` 또는 venv activate 스크립트에 추가:
```bash
export ASR_RAW_DATA_ROOT=/home/jake/MyProject/stt/data-gen/aig-audio-3/data/raw
```

또는 프로젝트 상대 경로를 쓰고 싶으면 심볼릭 링크:
```bash
ln -s /home/jake/MyProject/stt/data-gen/aig-audio-3/data/raw data/raw
```

### 0.2 Git 초기화

Git 이 아직 없으면 Phase 1 시작 전에 초기화한다. `data/raw/`, `.cache/`, `runs/`,
`.venv/`, `__pycache__/`, `*.pyc` 는 `.gitignore` 에 넣어 음원·라벨 본문과 실행 산출물이
커밋되지 않게 한다.

---

## Step 1 — 프로젝트 골격

빈 디렉토리 + 의존성 + 자리만 잡기.

### 1.1 디렉토리 생성
```
mkdir -p workspace judge frozen scripts baseline runs tests \
         .cache/ct2_models notes assets/audio_profile
touch workspace/__init__.py judge/__init__.py frozen/__init__.py
```

### 1.2 의존성

`pyproject.toml` 또는 `requirements.txt` 에 버전을 고정한다. 아래는 초기 후보이며, 실제
설치 검증 후 lock 파일 또는 requirements 에 확정값을 남긴다.

```text
ctranslate2==4.5.0
faster-whisper==1.0.3
transformers==4.45.2
librosa==0.10.2
soundfile==0.12.1
numpy==1.26.4
rapidfuzz==3.10.0
huggingface_hub==0.25.2
silero-vad==5.1
pytest==8.3.3
```

venv 생성 + 설치.

### 1.3 모델 캐시 준비 (선택, 첫 실행 시 자동도 OK)

`openai/whisper-large-v3-turbo` HF → CT2 변환:

```bash
ct2-transformers-converter \
  --model openai/whisper-large-v3-turbo \
  --output_dir .cache/ct2_models/whisper-large-v3-turbo \
  --quantization float16
```

**검증**: 디렉토리에 `model.bin`, `config.json`, `tokenizer.json` 존재.

---

## Step 2 — Judge: 페어링 + 정규화

판정 신뢰성의 기반. 이거 틀리면 baseline/σ/모든 점수가 무의미.

### 2.1 `judge/pairing.py`

함수: `def pair_batch(batch_name: str, root: Path = ...) -> list[tuple[Path, Path]]:`

규칙 (`STT-PIPELINE-SPEC.md §3.4`):
1. `root/label/<batch>/*.txt` iterate
2. `*_l.txt` 패턴만 통과
3. 같은 basename 의 `root/wav/<batch>/<base>.wav` 페어링
4. wav 없는 label → skip + log warning
5. `ASR_RAW_DATA_ROOT` env var 로 root 오버라이드

**검증**: 0715 에 대해 정확히 12 페어 반환.

### 2.2 `judge/normalize.py`

함수: `def normalize(text: str) -> str:`

[`STT-PIPELINE-SPEC.md §5.1`](STT-PIPELINE-SPEC.md) 의 1~6 단계 문자열 변환을 그대로
구현한다 (NFC, INAUDIBLE 제거, 구두점 제거, lowercase, 숫자 유지, whitespace 제거).
명세 본문이 정본이므로 단계 텍스트는 본 문서에 중복하지 않는다.

`normalize()` 는 `str -> str` 만 담당. Levenshtein edit distance·editops 산출은
`judge/metrics.py` 책임.

### 2.3 Label 파서 (`judge/pairing.py` 안 또는 별도)

함수: `def parse_label(path: Path) -> str:`

§4.2 규칙:
- 숫자만 있는 줄 → 버림 (turn 번호)
- 빈 줄 → 버림
- 나머지 줄을 공백 한 칸으로 concat → 한 줄 reference

**검증 (이 Step 종료 시)**:
- 0715 label 12개 모두 정상 파싱 (빈 reference 없음)
- `normalize("[INAUDIBLE] 안녕하세요!")` → `"안녕하세요"`
- 동일 정규화 함수가 hypothesis 와 reference 양쪽에 쓰일 것을 의식하고 작성

---

## Step 3 — Judge: 메트릭

### 3.1 `judge/metrics.py`

핵심 함수:

```
def per_file_metrics(
    ref_norm: str,
    hyp_norm: str,
    hyp_raw: str,
    audio_s: float,
    decode_s: float,
    audio_coverage_s: float | None = None,
) -> dict:
    # ref_chars, hyp_chars, length_ratio, empty_output,
    # sub, del, ins, edits, cer, rtf,
    # hallucination_hits, hallucinated_spans
    ...

def corpus_aggregate(per_file: list[dict]) -> dict:
    # corpus_cer, macro_cer,
    # error_breakdown {sub_ratio, del_ratio, ins_ratio},
    # empty_output_rate, length_ratio {mean, p05, p95},
    # repeated_text_rate, audio_coverage_rate,
    # hallucination_hit_rate, hallucination_hits_total,
    # total_inference_time_s, runtime_s_per_audio_min, avg_rtf
    ...
```

`hallucination_hits` 는 **정규화 *전* 원본 hyp** 에 대해 §6.4 패턴 regex 매치.

`audio_coverage_s` 는 optional sidecar telemetry 에서 읽는다. 없으면 per-file 값은
`null` 또는 omit 하고, corpus `audio_coverage_rate` 는 hard gate 에 쓰지 않는다.

`repeated_text_rate` 검출은 단순 휴리스틱으로 시작 (예: 동일 4-gram 이상이 3회 이상
연속 등장하는 파일 비율). Phase 1 정밀도는 중요치 않음 — 가드 임계는 Phase 3 에서
조정.

### 3.2 `judge/diagnosis.py`

에이전트가 다음 가설을 세울 수 있도록, 점수와 오디오 특성을 결합한
`runs/<hyp_id>/diagnosis_report.json` 을 만든다. 원본 `assets/audio_profile/*.json`
전체를 노출하지 않고, 0715 12개 파일 모두의 요약 summary 를 붙인다. 구체적인
VAD 경계는 숨기고, focus file 은 그중 우선 볼 파일 최대 2개를 표시하는 인덱스다.

**focus file 선정 (deterministic, 최대 2개)**:
1. guard 위반 수가 많은 파일
2. per-file CER 가 높은 파일
3. baseline per-file CER 대비 악화폭이 큰 파일 (baseline 이 있을 때)
4. tie-break = path lexical sort

summary 는 모든 파일에 제공한다. 이렇게 해야 LLM 이 특정 1-2개 파일만 보고 과잉
일반화하지 않고 전체 분포를 볼 수 있다. 대신 raw `speech_segments` 는 제공하지 않는다.

```json
{
  "per_file_diagnosis": [
    {
      "wav": "data/raw/wav/AIG_녹취반출_20250715/<...>_l.wav",
      "metrics": {
        "cer": 0.42,
        "length_ratio": 0.31,
        "hallucination_hits": 0
      },
      "audio_profile_summary": {
        "duration_s": 1234.56,
        "silence_ratio": 0.18,
        "longest_silence_s": 12.3,
        "longest_speech_s": 240.1,
        "rms_db_mean": -22.1,
        "rms_db_p05": -38.4,
        "rms_db_p95": -15.2,
        "speech_segment_summary": {
          "num_segments": 42,
          "speech_s_p50": 8.2,
          "speech_s_p95": 64.1,
          "silence_gap_p95": 11.7
        }
      },
      "flags": ["length_ratio_low"]
    }
  ],
  "focus_files": [
    {
      "wav": "data/raw/wav/AIG_녹취반출_20250715/<...>_l.wav",
      "why_selected": ["guard_violation", "worst_cer"]
    }
  ]
}
```

`speech_segments` 원본 경계 리스트는 diagnosis 에 복사하지 않는다. 구체적인
start/end 경계를 그대로 주면 그 자체가 chunking recipe 가 되므로, LLM 이 원인을
추론할 수 있을 만큼의 분포 요약만 제공한다.

### 3.3 단위 점검 (간이)

수작업 골든 케이스 1~2개:
- `ref="안녕하세요" hyp="안녕"` → sub=0, del=3, ins=0, edits=3, cer=0.6
- corpus 집계 산술 검증 (`Σ edits / Σ ref_chars == corpus_cer ± 1e-9`)

Phase 1 에서는 얇은 pytest 를 둔다.
- `tests/test_normalize.py`: 정규화 골든 케이스
- `tests/test_pairing.py`: 0715 12 페어 매칭
- `tests/test_metrics.py`: editops 산술과 corpus 집계
- `tests/test_evaluate_smoke.py`: fake audio + stub transcribe end-to-end

---

## Step 4 — Judge: Entry + Verify

### 4.1 `judge/evaluate.py`

CLI:
```
python -m judge.evaluate \
    --batch AIG_녹취반출_20250715 \
    --transcribe workspace.transcribe:transcribe \
    --out runs/<hyp_id>/score_report.json
```

흐름:
1. `pairing.pair_batch(batch)` 로 페어 로드
2. `transcribe` callable 동적 import (`importlib`)
3. 각 wav: `librosa.load(sr=16000, mono=True)` → telemetry env 설정 → `transcribe(audio, sr)` → 측정 시간 기록
4. `parse_label` + `normalize` 로 ref_norm, hyp_norm
5. `per_file_metrics` → `corpus_aggregate`
6. `score_report.json` 작성 (DESIGN §2.5 형식)
7. `per_file.jsonl` 작성
8. `assets/audio_profile/` 원본에서 전체 12파일 summary + focus file 최대 2개를 뽑아
   `diagnosis_report.json` 작성
9. **마지막 줄에 corpus_cer 한 숫자만 print** — autoresearch Verify 파싱용

Step 7 전에는 audio profile 이 아직 없으므로 diagnosis 의 profile excerpt 는
skip/warning 가능. Phase 3 진입 전 최종 verify 에서는 반드시 profile excerpt 가
채워져야 한다.

telemetry env:
- `ASR_TELEMETRY_DIR=runs/<hyp_id>/_telemetry`
- `ASR_TELEMETRY_FILE_ID=<wav stem>`

`transcribe()` 반환 계약은 계속 `str` 이다. pipeline 이 위 env 를 사용해
`<file_id>.jsonl` sidecar 를 쓰면 judge 가 `audio_coverage_s` 를 산출하고, 없으면 coverage
가드는 warning + skip 으로 처리한다.

### 4.2 `scripts/verify.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
HYP_ID="${HYP_ID:-manual_$(date +%s)}"
mkdir -p "runs/${HYP_ID}"
exec python -m judge.evaluate \
    --batch AIG_녹취반출_20250715 \
    --transcribe workspace.transcribe:transcribe \
    --out "runs/${HYP_ID}/score_report.json"
```

Phase 1 에서는 가드 검사 없음. exit code 는 judge 의 정상/예외만 반영.

**검증**: 일단 transcribe 가 없으므로 ImportError 로 깨질 것. Step 5/6 에서 frozen
+ 스텁 만든 뒤 함께 검증.

---

## Step 5 — Frozen layer (`frozen/asr_backend.py`)

backend (모델·디바이스·precision) 를 봉인. workspace 는 이 layer 의 세 helper
(`load` / `generate` / `to_storage_view`) 만 호출. Phase 3 진입 시 `frozen/` 은 편집 금지.

### 5.1 시그너처

```python
# frozen/asr_backend.py — 편집 금지 (Phase 3)
import ctranslate2
from transformers import WhisperProcessor

_MODEL_NAME       = "openai/whisper-large-v3-turbo"   # hard-coded
_MODEL_CACHE_PATH = ".cache/ct2_models/whisper-large-v3-turbo"
_DEVICE           = "cuda"
_COMPUTE_TYPE     = "float16"

_model = None
_processor = None

def load() -> tuple:
    """(Whisper, WhisperProcessor) 반환. 모델/디바이스/precision 고정·캐시."""
    global _model, _processor
    if _model is None:
        _processor = WhisperProcessor.from_pretrained(_MODEL_NAME)
        _model = ctranslate2.models.Whisper(
            _MODEL_CACHE_PATH, device=_DEVICE, compute_type=_COMPUTE_TYPE,
        )
    return _model, _processor


def generate(features, prompts, **decoding_kwargs):
    """ctranslate2 model.generate 호출. decoding_kwargs 는 자유 passthrough.
    (beam_size, temperature, length_penalty, repetition_penalty, sampling_*, 등)
    """
    model, _ = load()
    return model.generate(features, prompts, **decoding_kwargs)


def to_storage_view(np_array):
    """numpy array → ctranslate2.StorageView. workspace 가 ctranslate2 import 없이 사용."""
    return ctranslate2.StorageView.from_array(np_array)
```

### 5.2 워크스페이스에 노출되는 것 / 막히는 것

| 자유 | 봉인 |
|------|------|
| decoding kwargs (beam, temperature, fallback options 전부) | 모델 이름·경로·디바이스·precision |
| chunking, prompt 구성, feature extraction 정책 | ctranslate2 직접 import (workspace 는 frozen helper 만 사용) |
| post-processing, merging | model.generate 외 ctranslate2 API |

### 5.3 검증

```
python -c "from frozen.asr_backend import load, generate, to_storage_view; m, p = load(); print('ok')"
```

→ 첫 호출 시 모델 다운로드/변환·로드 완료까지 시간 걸림. 이후 캐시.

---

## Step 6 — Workspace 초기 스텁

`workspace/transcribe.py`:

가장 단순한 호출 — `frozen.load()` / `frozen.generate()` / `frozen.to_storage_view()`
세 helper 만 사용, chunking 없이 1 회. 30 초 초과는 깨져도 좋음 (오히려 권장 —
autoresearch 가 풀 출발점).

```python
import numpy as np
from frozen.asr_backend import load, generate, to_storage_view

def transcribe(audio: np.ndarray, sr: int) -> str:
    model, proc = load()
    inputs = proc(audio, sampling_rate=sr, return_tensors="np")
    features = to_storage_view(inputs.input_features)
    prompt = proc.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", "<|ko|>", "<|transcribe|>", "<|notimestamps|>"]
    )
    # decoding kwargs 자유 — 아래는 가장 단순한 greedy
    results = generate(features, [prompt], beam_size=1, sampling_temperature=0.0)
    tokens = results[0].sequences_ids[0]
    return proc.tokenizer.decode(tokens, skip_special_tokens=True)
```

> 위는 의사 코드. 실제 ctranslate2 API 와 일치하지 않을 수 있으므로 첫 작성 시
> 공식 문서 확인. **workspace 는 ctranslate2 / transformers 를 직접 import 하지 않는다**
> — `frozen.asr_backend` 의 `load / generate / to_storage_view` 만 사용. (Phase 3 정적 검사로 강제)

### 6.1 수동 스모크

```
bash scripts/verify.sh
```

기대: `runs/manual_*/score_report.json` 생성 + 마지막 줄에 corpus_cer 숫자. 점수는
**나빠도 OK** — 돌기만 하면 됨. `diagnosis_report.json` 의 profile excerpt 는 Step 7
audio profile 생성 후 최종 verify 에서 확인한다.

**검증**:
- exit code 0
- corpus_cer ≤ 1.5 정도 (말도 안 되게 큰 값이면 정규화/페어링 의심)
- per_file 12 행
- hallucination_hits 들이 산출됨

---

## Step 7 — Audio profile build (`scripts/build_audio_profile.py`)

정답 라벨과 *무관* 한 오디오 자체 특성을 한 번 산출 → `assets/audio_profile/<batch>.json`
에 봉인. Phase 3 매 iter 의 `diagnosis_report.json` 과 잡 종료 후 분석에서
"긴 무음 구간에서 깨졌는가" 같은 가설 검증에 참조.

**적용 대상**: 0715 (eval) 만. **holdout (0813) profile 은 Phase 3 *전* 절대 생성
하지 않음** — holdout 접근 금지 원칙.

### 7.1 산출 필드 (audio-only)

```json
{
  "batch": "AIG_녹취반출_20250715",
  "method": "silero-vad v5.1 + RMS aggregates (librosa)",
  "produced_at": "2026-05-28T...",
  "per_file": [
    {
      "wav": "data/raw/wav/AIG_녹취반출_20250715/<...>_l.wav",
      "duration_s": 1234.56,
      "speech_segments": [[start_s, end_s], ...],
      "silence_ratio": 0.18,
      "longest_silence_s": 12.3,
      "longest_speech_s": 240.1,
      "rms_db_mean": -22.1,
      "rms_db_p05": -38.4,
      "rms_db_p95": -15.2
    },
    ...
  ]
}
```

**라벨 기반 필드 (예: speech_rate_proxy = ref_chars / speech_s) 는 여기 넣지 않음** —
audio-only 원칙. 필요하면 별도 `assets/label_profile/<batch>.json` 으로 분리.

### 7.2 구현 메모

- VAD: **silero-vad** (`silero-vad==5.1`, PyTorch 의존). librosa.effects.split 보다
  speech/silence 경계 정확.
- RMS aggregates 는 librosa.
- 산출 1 회 봉인. Phase 3 에서 `workspace/transcribe.py` 는 원본 profile 파일을
  직접 참조하거나 읽지 않는다.
- `judge/evaluate.py` 와 `scripts/analyze_run.py` 만 원본 profile 을 읽는다.
  에이전트에는 매 iter `diagnosis_report.json` 의 전체 파일 summary 와 focus file 표시만 노출한다.
- `speech_segments` 전체 start/end 리스트는 원본 asset 에만 보관하고, diagnosis 에는
  segment 개수·분위수 같은 요약만 제공한다. 구체 경계는 chunking 힌트가 되기 때문.

### 7.3 검증

- per_file 12 행 (0715)
- duration_s 합산이 12 페어 wav 의 librosa.get_duration 합과 일치
- speech_segments 가 [0, duration_s] 안에 들어옴
- profile 생성 후 `bash scripts/verify.sh` 재실행 시 `diagnosis_report.json` 에
  per_file_diagnosis 12개, focus file ≤ 2, raw `speech_segments` 미포함

---

## Step 8 — Baseline 측정 (faster-whisper)

### 8.1 `scripts/measure_baseline.py`

흐름:
1. faster-whisper 로드 (`large-v3-turbo`, float16, GPU)
2. 0715 12 페어 iterate → `model.transcribe(wav)` → text 합치기
3. **동일 judge 의 normalize + metrics** 사용 (transcribe 만 다른 백엔드)
4. `baseline/target_cer.json` 작성 (DESIGN §2.8 형식)

### 8.2 봉인

- 작성 후 git 커밋
- 파일 상단 또는 `baseline/README.md` 에 "재실행 금지" 명시
- chmod 444 (선택)
- `versions`, `model`, `decoding_params`, `hardware` 메타데이터 기록

**검증**:
- `target_cer` 가 합리적 범위 (보험 콜센터 한국어로 0.05~0.20 추정)
- `total_inference_time_s`, `total_audio_s` 기록됨
- faster-whisper / ctranslate2 / transformers 버전, HF revision, decoding params 기록됨
- per_file 12 행, edits 합산이 corpus_cer 와 일치

---

## Step 9 — σ 측정

### 9.1 `scripts/measure_sigma.py`

**비용 절감**: corpus 전체 12 페어 × 3 회 (=수십 시간) 대신 **대표 파일 1 개 × 3 회**.
SPEC §6.1 의 representative-file proxy 옵션. 정직하게 *근사* 임을 기록.

흐름:
1. **대표 파일 선정**: 0715 _l.wav 중 `audio_s` 최장. tie-break = path lexical sort.
   `assets/audio_profile/AIG_녹취반출_20250715.json` 의 `duration_s` 로 선정.
2. `workspace/transcribe.py` (Step 6 스텁) 로 *해당 파일 1 개만* 평가 3 회 반복
3. 매번 새 `HYP_ID` 로 `runs/` 에 저장 (judge 는 single-file 모드 또는 batch 무시)
4. 그 파일의 cer 3 개 표준편차 → `baseline/noise_floor.json`

### 9.2 산출 스키마

```json
{
  "scope": "representative_file_proxy",
  "method": "longest _l.wav by audio_s, 3 runs, lexical tie-break",
  "representative_file": "data/raw/wav/AIG_녹취반출_20250715/<...>_l.wav",
  "representative_audio_s": ...,
  "samples": [0.XXX, 0.XXX, 0.XXX],
  "sigma": 0.00XX,
  "applies_to": "corpus_cer Δ threshold (근사 — corpus σ 와 동일성 보장 X)",
  "measured_against": "initial transcribe stub",
  "measured_at": "2026-05-28T..."
}
```

### 9.3 스텁이 너무 깨졌을 때

대표 파일 cer 이 1.0 이상 (= 사실상 빈 출력) 이거나 매 실행 동일 (= 결정론) 이면
σ ≈ 0 으로 나옴. 이 경우 σ 측정은 **Phase 3 첫 정상 가설 이후로 미룸** — 노이즈
임계 적용을 그만큼 늦춤.

**검증**:
- 3 samples 기록 + sigma + representative_file 명시
- σ 가 0 이 아니면 OK, 0 이면 위 노트 적용

---

## Step 10 — DoD 점검

`DESIGN.md §2.10` 체크리스트 그대로:

- [ ] 데이터 페어링 코드가 0715 12 페어 정확히 매칭
- [ ] judge 가 스텁 transcribe 에 대해 score_report.json 산출
- [ ] `scripts/verify.sh` 실행 시 corpus_cer 숫자가 마지막 줄에 출력
- [ ] `runs/<hyp_id>/diagnosis_report.json` 생성 — per_file_diagnosis 12개, focus file 최대 2개, raw `speech_segments` 미포함
- [ ] `assets/audio_profile/AIG_녹취반출_20250715.json` 생성 (0715 only — 0813 미생성)
- [ ] `baseline/target_cer.json` 생성 + 봉인
- [ ] `baseline/target_cer.json` 에 versions/model/decoding_params/hardware 메타데이터 기록
- [ ] `baseline/noise_floor.json` 생성 — representative-file proxy 스키마 (scope/method/representative_file/samples/sigma)
- [ ] `pytest` 로 normalize/pairing/metrics/evaluate smoke 통과
- [ ] 사람이 수동으로 verify.sh 1 회 돌려서 cer 숫자 확인
- [ ] holdout 배치명은 `workspace/`, `judge/`, prompt, 운영 wrapper 를 제외한 `scripts/` 에서
      참조하지 않음 (`rg -n "0813|20250813|AIG_녹취반출_20250813" workspace judge scripts --glob '!scripts/seal_holdout.sh'` → 빈 결과)

전부 통과 → Phase 2 플랜으로 진행.

---

## 진행 원칙

- 각 Step 종료 시 **수동 검증** 한 번 — 다음 Step 들어가기 전에 깨끗한 상태 확인
- Step 2~3 (judge) 가 가장 중요 — 시간 더 써도 OK
- Step 6 스텁은 빨리 — autoresearch 가 어차피 다 갈아엎음
- 막히면 멈추고 사용자에 보고 (특히 모델 변환 / GPU 메모리 / 데이터 위치)

---

## 다음 단계

Phase 1 DoD 전부 통과 → [`PHASE2-PLAN.md`](PHASE2-PLAN.md) (평가 인프라 구축) →
[`PHASE3-PLAN.md`](PHASE3-PLAN.md) (autoresearch 실행 + 분석).
