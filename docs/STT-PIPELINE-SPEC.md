# STT Pipeline 고도화 — 문제 정의 (이식용 명세)

> **목적**: 이 문서는 어떤 프레임워크/아키텍처로 옮겨도 그대로 문제를 정의할 수 있도록
> 작성된 **이식 가능한 STT 파이프라인 명세**다. 자동화 하네스, 에이전트 루프, 잠금
> 메커니즘 같은 운영 인프라는 일체 포함하지 않는다 — **문제, 데이터, 평가, 제약**만.

---

## 0. 한 줄 요약

한국어 보험 콜센터 통화에 대해, **ctranslate2 + Whisper-large-v3-turbo** 백엔드를
고정하고, 그 위에 임의의 추론 파이프라인을 구성해 **corpus-level CER을
faster-whisper baseline 이하**로 낮춘다.

---

## 1. 목표

| 항목 | 값 |
|------|------|
| **Primary metric** | `corpus_cer` (corpus-level character error rate) |
| **Target** | `corpus_cer ≤ target_cer` (target_cer = faster-whisper baseline, §7) |
| **Constraints** | 품질 가드 + 속도 가드 (§6) |
| **개선 단위** | `Δcer ≥ 2σ` (σ = 평가 노이즈 floor, §6.1) 일 때만 의미 있는 개선 |

목표 절대값은 baseline 측정 후 확정. 시작 시점에서는 "faster-whisper를 능가" 라는
상대적 목표.

---

## 2. Backend / Model (고정)

| 항목 | 값 |
|------|------|
| Library | `ctranslate2` (직접 사용. faster-whisper는 비교 기준일 뿐 사용 금지) |
| Model | `openai/whisper-large-v3-turbo` (CT2 변환) |
| 변환 캐시 | `.cache/ct2_models/` (첫 실행 시 자동 변환) |
| Runtime | CUDA + float16 (로컬 GPU) |
| Audio 사양 | 16 kHz mono float32 numpy array |

**Backend 변경 금지** — 다른 모델/라이브러리로 바꾸면 동일 데이터의 CER을 다른
환경에서 비교할 수 없게 된다. 본 문제는 *동일 모델 위의 파이프라인 차이* 를
측정한다.

---

## 3. 데이터셋

### 3.1 출처와 위치

- **출처**: AIG 보험 상담 통화 녹취
- **경로**:
  - wav: `data/raw/wav/AIG_녹취반출_<batch>/*.wav`
  - label: `data/raw/label/AIG_녹취반출_<batch>/*.txt`
  - 환경변수 `ASR_RAW_DATA_ROOT`로 `data/raw` 절대경로를 오버라이드 가능 (기본 `data/raw`).

### 3.2 음원 사양

| 항목 | 값 |
|------|------|
| 원본 | 8 kHz 전화 통화 |
| 처리본 | **16 kHz 업샘플링 mono** (`.wav`) |
| 길이 | 30 초 초과 long-form 다수. 일부는 수 분 이상 |
| 잡음 | 배경 잡음·끊김 가능 (전화 음질) |

### 3.3 채널 정책 — `*_l` only, 무조건

모든 페어링은 **left channel 파일만** 사용 (`*_l.wav` ↔ `*_l.txt`).

- 화자: 상담사 측 (counselor side)
- `*_r.{wav,txt}` 같은 다른 채널 파일이 함께 있어도 무시
- sanity check 단계 없음 — left-only는 무조건

### 3.4 페어링 — Label-driven

label 파일을 source of truth로 한다 (label이 wav보다 적거나 같음).

- `label/<batch>/*.txt`를 iterate
- 같은 basename의 `wav/<batch>/<base>.wav`를 페어링
- `*_l.txt` 만 통과, 그 외 패턴 스킵
- wav 없는 label은 skip + 경고

### 3.5 Batches

| Batch | _l 페어 수 | 용도 |
|-------|------------|------|
| `AIG_녹취반출_20250715` | 12 | eval (primary) |
| `AIG_녹취반출_20250813` | 13 | **holdout — 접근 금지 (§8)** |

> 운영 환경에 `AIG_녹취반출_20250704` 등 다른 batch 가 함께 존재할 수 있으나,
> 본 문제는 위 두 batch 만 사용한다. pairing 단계에서 다른 batch 는 통과하지
> 않는다.

---

## 4. Reference label 포맷

### 4.1 파일 구조

```
1
[INAUDIBLE] (turn 1 텍스트 ...)

2
(turn 2 텍스트, 여러 줄 가능 ...)
(긴 문장 줄바꿈 가능 ...)

3
(turn 3 텍스트 ...)
...
```

### 4.2 파싱 규칙

1. **숫자만 있는 줄** = turn 번호 → 버림
2. **빈 줄** = turn 구분 → 버림
3. 각 turn은 여러 줄로 구성될 수 있음 (긴 문장 줄바꿈)
4. turn 텍스트는 공백 한 칸으로 concat → 한 줄 reference로 정리
5. **시간 정보 (start/end) 없음** — 파일 단위 비교만 가능, chunk-level 비교 불가

### 4.3 `[INAUDIBLE]` 토큰

- 들리지 않는 구간 표시
- CER 정규화에서 **제거됨** → 가설(hypothesis)에서 이 구간에 무엇을 출력하든 점수에
  영향 없음
- 따라서 `[INAUDIBLE]` 처리 정책은 점수 최적화에서 자유롭게 결정

---

## 5. 평가 (Evaluation)

### 5.1 CER 정규화 — 양쪽 텍스트 동일 적용

다음 순서로 hypothesis와 reference 양쪽에 동일하게 적용한다:

1. **Unicode NFC** 정규화
2. **`[INAUDIBLE]` 토큰 제거** (case-insensitive)
3. **구두점 제거**: `. , ? ! … 「 」 『 』 ( ) " ' ` ~ ! @ # $ % ^ & * _ + = - : ; / \ < > | · 、 。 “ ” ‘ ’ 《 》 〈 〉 【 】 〔 〕`
4. **영문 lowercase**
5. **숫자는 그대로 비교** (한글 변환 X)
6. **모든 whitespace 제거** (space, tab, newline, U+3000 등)
7. 위 정규화된 두 문자열로 **character-level Levenshtein edit distance** 계산

### 5.2 집계

| 지표 | 정의 | 용도 |
|------|------|------|
| **`corpus_cer`** | `Σ_i edits_i / Σ_i ref_chars_i` (char-weighted) | **Primary, 모든 의사결정의 기준** |
| `macro_cer` | per-file CER의 산술 평균 | 진단용 보조 지표 |

> corpus-level이 의사결정 기준인 이유: long-form 파일들 사이 길이 편차가 커서,
> 짧은 파일의 CER이 macro 평균을 왜곡할 수 있다. 글자 가중치가 공정.

### 5.3 Edit-op 분해

character-level Levenshtein editops로 sub/del/ins 카운트 분해:

| 카운트 | 의미 | 가설 방향 신호 (예시) |
|--------|------|----------------------|
| `sub` | substitution | 도메인 용어 오인식 가능성 |
| `del` | deletion | 경계 손실/누락 가능성 |
| `ins` | insertion | 환각/반복 가능성 |

per-file과 corpus totals를 모두 산출. corpus ratio:

```
sub_ratio + del_ratio + ins_ratio = 1.0
```

---

## 6. 품질 지표 (Guards)

corpus_cer 하나만 보면 회귀의 원인을 알 수 없다. 다음 가드를 함께 산출하여
**의사결정 + 진단**에 사용한다.

### 6.1 의미 있는 개선의 정의 — Noise floor σ

같은 평가 대상 파이프라인을 **3회 이상 반복 실행**해 `corpus_cer` 분포의 표준편차 σ를
추정한다. 디코딩 비결정성 때문에 같은 코드도 매 실행 값이 다르다.

- `Δcer ≥ 2σ` 만 "의미 있는 개선" — 그 이하는 노이즈로 간주
- 측정 대상과 시점은 운영 설계에서 정하고, σ는 한 번 산출한 뒤 잡 동안 고정

### 6.2 Corpus-level 가드

| 지표 | 정의 |
|------|------|
| `corpus_cer` | primary metric |
| `macro_cer` | 보조 |
| `error_breakdown.{sub,del,ins}_ratio` | edit-op 분해 비율 |
| `empty_output_rate` | hyp가 빈 문자열인 파일 비율 |
| `length_ratio_mean` / `p05` / `p95` | hyp_chars / ref_chars 분포 (디코더가 너무 짧/길게 출력하지 않는지) |
| `repeated_text_rate` | 반복 문장/구절이 검출된 파일 비율 (반복 점검) |
| `audio_coverage_rate` | 디코더가 처리한 누적 audio_s / 전체 audio_s. pipeline 이 telemetry 를 제공할 때 산출하며, 미제공 시 hard gate 에 쓰지 않는다 |
| **`hallucination_hit_rate`** | **§6.4 패턴 중 어느 하나라도 hyp에 매치된 파일 비율 (Whisper 알려진 환각 검출)** |
| `hallucination_hits_total` | 모든 파일·모든 패턴 누적 매치 횟수 (per-file `hallucination_hits` 합) |
| **`total_inference_time_s`** | **12 파일 전체 처리 wall clock 합** (절대 시간) |
| `runtime_s_per_audio_min` | `total_inference_time_s / (total_audio_s / 60)` (단위 시간당 처리 시간 — 1.0이면 실시간) |
| `avg_rtf` | per-file Real-Time Factor (`decode_s / audio_s`) 평균 |

### 6.3 Per-file (진단용)

각 파일마다 다음을 함께 기록 — 디버깅 / 회귀 원인 추적용.

| 필드 | 의미 |
|------|------|
| `wav` | 파일 경로 |
| `ref_chars`, `hyp_chars` | 정규화 후 글자 수 |
| `length_ratio` | `hyp_chars / ref_chars` |
| `empty_output` | hyp 비었는지 |
| `audio_s`, `decode_s` | 음원 길이 / 디코드 시간 |
| `cer`, `rtf` | per-file CER / RTF |
| `sub`, `del`, `ins`, `edits` | edit-op 카운트 (sub+del+ins == edits) |
| `audio_coverage_s` | 그 파일에서 디코더가 처리한 audio_s (optional telemetry 기반) |
| `hallucination_hits` | §6.4 패턴 매치 횟수 (정수, 패턴별 카운트 옵션) |
| `hallucinated_spans` | 매치된 패턴/스팬 리스트 (진단용, optional) |

### 6.4 Hallucination 패턴 검출

Whisper-large-v3-turbo는 한국어 음원에서도 학습 데이터(YouTube 자막 등) 잔재로
다음 패턴을 환각으로 뱉는 사례가 알려져 있다. 보험 콜센터 통화에는 절대 등장하지
않는 표현이므로, 이들이 hyp에 나타나면 **즉시 환각으로 간주**한다.

**패턴 (정규식, case-insensitive 권장)**

```yaml
blocking_patterns:
  - '기상캐스터\s*배혜지'
  - '시청자여러분'
  - '이\s*시각\s*세계였습니다'
  - '시청해주셔서\s*감사합니다'
  - '한글자막\s*by\s*.+'
  - '다음\s*영상에서\s*만나요'
  - '자막\s*제공'
  - '광고를\s*포함'
```

**적용 방식**

1. **검출 시점**: CER 정규화 *이전* 원본 hyp 문자열에 대해 `re.search` (regex)로
   매치 검사 — 정규화가 공백/구두점을 지워버리면 패턴 매칭 신뢰도가 떨어짐.
2. **카운트**:
   - per-file `hallucination_hits` = 매치된 (pattern, span) 쌍의 수
   - per-file `hallucinated_spans` = `[{pattern: "...", start: int, end: int, text: "..."}]` (optional, 진단)
3. **집계**:
   - `hallucination_hit_rate` = (`hallucination_hits > 0` 인 파일 수) / `num_files`
   - `hallucination_hits_total` = `Σ hallucination_hits`
4. **CER에는 영향 X**: 이 가드는 CER 계산을 변경하지 않는다. 별도 지표로 산출되어
   품질 게이트에서 임계를 두는 데 쓰인다 (예: `hallucination_hit_rate > 0.05` 면 reject).

**파이프라인 구현체의 선택**

이 패턴 리스트는 **평가자가 hyp에 대해 검사**하는 기준이지, 파이프라인이 반드시
사용해야 할 후처리는 아니다. 파이프라인은 자체적으로 같은(또는 더 정교한) 후처리를
적용해도 좋고, 안 해도 좋다 — 평가 결과로 검출되면 점수에 반영된다.

> 패턴 리스트는 시간이 지나면서 새로 발견된 환각으로 확장될 수 있다. 평가
> 무결성을 위해 한 잡 동안에는 리스트를 **고정**하고, 잡 사이에만 갱신한다.

---

## 7. Oracle Baseline (비교 기준)

### 7.1 정의

`faster-whisper` (CT2 위에 빌드된 핸드튠 추론 라이브러리)로 §3.5의 0715 12 _l
페어를 1회 transcribe하여 산출한 corpus_cer.

- 동일한 §5.1 정규화 적용
- 1회 측정 후 봉인 — 재측정 안 함 (결정론 보장)

### 7.2 위치 (이식 시 보관 형식 권장)

```json
{
  "target_cer": 0.0XXX,
  "macro_cer": 0.0XXX,
  "num_files": 12,
  "batches": ["AIG_녹취반출_20250715"],
  "total_audio_s": 1245.7,
  "total_inference_time_s": 24.7,
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
  "decoding_params": {"beam_size": 5, "temperature": 0.0},
  "hardware": {"gpu": "...", "compute_type": "float16"},
  "per_file": [
    {"wav": "...", "cer": 0.04..., "ref_chars": 412, "edits": 18,
     "audio_s": 87.3, "decode_s": 5.2}
  ]
}
```

### 7.3 의미

- `target_cer`는 **달성해야 할 상한** — 추론 파이프라인이 이 값 이하로 가면 성공
- faster-whisper 자체는 **비교 기준일 뿐 해법이 아님** — 우리 파이프라인은
  ctranslate2 raw로 직접 구성

---

## 8. Holdout 정책

### 8.1 대상

- `AIG_녹취반출_20250813` (13 _l 페어, ≈ 4 시간)

### 8.2 정책

- **모든 형태의 접근 금지**:
  - hyperparameter tuning에 사용 X
  - 중간 점검 평가에 사용 X
  - 어떤 통계도 산출 X
- 최종 잡 종료 후 사용자가 **수동으로 1회만** 평가

### 8.3 이식 시 보호

이 디렉토리는 운영 환경에서 file system 접근을 적극적으로 차단할 것 (deny list,
파일 권한, 별도 마운트 등).

---

## 9. 도메인 컨텍스트

### 9.1 음원/화자

- 한국어 보험 상담 통화
- 화자: 상담사 (존댓말, 격식체 — 보험사 매뉴얼 화법) + 고객 (다양)
- left channel = 상담사 측만 사용
- 원래 8 kHz 전화 통화 → 16 kHz mono 업샘플 → 통화 음질 특유의 잡음/끊김 존재

### 9.2 특이 발화 패턴 — 약관 낭독

상담사가 약관을 매우 빠른 속도로 발음을 뭉개며 읽는 구간이 흔하다.

- 결과적으로 **무음(silence)이 거의 없는 수 분 이상의 긴 연속 발화**가 존재
- 단순 silence-based 분할은 경계를 찾기 어려움
- 30초 초과 long-form 처리 전략이 핵심 난점

### 9.3 자주 등장하는 용어 카테고리

> *카테고리* 만 명시. 실제 라벨 본문의 문장은 인용하지 않는다 (§11 leakage 정책).

- **보험**: 보험료, 갱신, 청약(철회), 약관, 면책, 담보, 특약, 진단비, 보장
- **의료**: 첩약, 약침, 한방치료, 골절, 응급실 내원비, 통합간호간병, 입원비
- **금액**: 콤마 포함 표기와 콜로퀴얼(예: "이만 삼천 원") 혼재
- **전화번호**: 하이픈 포함/미포함 혼재

### 9.4 라벨 표기 규약

- 금액: 콤마 포함 한 단어 형태 (가공 예시: `12,345원`)
- 전화번호: 혼재
- Whisper large-v3-turbo는 콜로퀴얼을 풀어쓰는 경향이 있어, 라벨과 표기가 어긋날 수
  있다 → 후처리에서 digit 변환 고려 가능

### 9.5 Whisper 한국어 특유 실패 패턴 (관찰)

| 패턴 | 설명 |
|------|------|
| 짧은 호응 누락 | "네", "음", "예" 같은 짧은 응답이 빠짐 |
| 영문 약어 표기 흔들림 | "OK" ↔ "오케이" |
| 긴 발화 중간 truncation | 30초 한계 부근에서 끊김 |
| 동음이의어 혼동 | 보장↔보상, 청구↔청약 |
| **학습 데이터 잔재 환각** | YouTube 자막류 ("시청해주셔서 감사합니다", "한글자막 by ...", "기상캐스터 배혜지" 등). 보험 콜센터 음원에는 등장 불가 — §6.4 패턴 리스트 참조 |

이는 *현상* 의 기술이지 *해결책* 의 기술이 아니다. 해결책은 파이프라인 구성의 결과로
나와야 한다.

---

## 10. 파이프라인 I/O 계약

추론 파이프라인은 다음 인터페이스 하나만 만족하면 된다. 내부 구조·알고리즘·프레임워크는
모두 자유.

```python
import numpy as np

def transcribe(audio: np.ndarray, sr: int) -> str:
    """
    Args:
        audio: float32 mono numpy array, shape (T,).
               Sample rate is 16000 Hz; values typically in [-1.0, 1.0].
               T can be anything from a few seconds to several minutes.
        sr:    integer, always 16000.

    Returns:
        Korean transcription as a single string. The evaluator applies
        normalization (§5.1) before scoring, so output capitalization,
        whitespace, and punctuation do not affect CER directly.
    """
    ...
```

### 10.1 호출 방식

평가자는 `data/raw/wav/AIG_녹취반출_20250715/` 의 각 `_l.wav` 파일에 대해 이 함수를
한 번씩 호출하고, §5 절차로 점수를 산출한다.

```python
# 평가자 예시 (참고용 — 실제 평가는 구현체 무관)
import librosa

for wav_path in sorted(glob("data/raw/wav/AIG_녹취반출_20250715/*_l.wav")):
    audio, sr = librosa.load(wav_path, sr=16000, mono=True)
    hyp = transcribe(audio.astype("float32"), sr)
    # → 채점
```

### 10.2 진단 telemetry (선택)

가드 산출과 회귀 추적을 위해 chunk/segment-level telemetry를 **옵셔널** 로 emit하는
것을 권장한다. `transcribe(audio, sr) -> str` 반환 계약은 바꾸지 않으며, 구현체가
sidecar log file 같은 외부 채널을 쓰는 방식이 기본이다.

권장 telemetry 필드 (구현 시 채울 수 있는 만큼만):

```
chunk_idx, start_s, end_s, duration_s,
overlap_left_s, overlap_right_s,
decode_params, fallback_used, fallback_attempts, selected_temperature,
decoded_text_len, chars_per_sec, segment_count,
avg_logprob, compression_ratio, runtime_s,
text_pre_merge_len, text_post_merge_len, overlap_dedup_chars
```

`audio_coverage_rate` 를 hard gate 로 쓰려면 telemetry 에 파일별 처리 구간 또는
`audio_coverage_s` 를 제공해야 한다. telemetry 가 없으면 evaluator 는 coverage 값을
`null` 또는 omit 하고, coverage hard gate 는 적용하지 않는다.

`no_speech_prob`는 Whisper-large-v3-turbo에서 부정확하므로 *판정에 쓰지 않는다*.
기록은 선택.

---

## 11. 절대 금지

| 항목 | 사유 |
|------|------|
| Holdout (`AIG_녹취반출_20250813`) 접근 | 일반화 검증의 무결성 |
| Eval label 본문을 prompt/후처리에 직접 주입 | 점수 조작 |
| 모델 fine-tuning | 본 문제는 학습이 아니라 추론 파이프라인 구성 |
| Backend 변경 (Whisper-large-v3-turbo + CT2 외) | 다른 환경의 CER과 비교 불가능해짐 |
| `[INAUDIBLE]` 토큰을 정답 측에서 의도적으로 활용 | 정규화 단계에서 제거되지만 그 사실을 점수 조작에 악용 X |

---

## 12. 부록 — 단일 책임 요약

| 단계 | 단일 책임 |
|------|----------|
| (1) Backend | 모델 가중치 동결 — 동일 출발선 보장 |
| (2) Dataset | label-driven, _l only, 0715 12 페어 |
| (3) Labels | turn 구조 + `[INAUDIBLE]` 제거 + 1줄 reference |
| (4) Normalization | NFC + INAUDIBLE 제거 + 구두점 제거 + lowercase + whitespace 제거 |
| (5) Aggregation | corpus-level char-weighted CER |
| (6) Decomposition | edit-op sub/del/ins |
| (7) Guards | length / empty / repeat / coverage / hallucination patterns / time |
| (8) Oracle | faster-whisper baseline = target 상한 |
| (9) Holdout | 0813, 잡 종료 후 1회만 |
| (10) Pipeline | `transcribe(audio, sr) -> str` 단일 계약, 내부 자유 |

---

**문서 종결**. 본 명세에 따라 구성된 어떤 파이프라인이든, 같은 backend·같은 정규화·같은
holdout 규율을 지키면 corpus_cer 숫자는 다른 환경의 결과와 직접 비교 가능하다.
