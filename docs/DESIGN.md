# AIG STT — 설계 문서

> **목적**: `STT-PIPELINE-SPEC.md` 의 문제를 `uditgoenka/autoresearch` (Claude Code
> 스킬, [`AUTORESEARCH.md`](AUTORESEARCH.md) 정본) 로 풀어 사람이 정한 `target_cer`
> 까지 corpus_cer 을 자동 진화로 낮춘다.
>
> 본 문서는 도메인 명세(문제 정의)는 다루지 않는다 — *어떻게 구축하고 어떻게
> 돌릴지* 만 다룬다.
> `SELF-EVOLVE-HARNESS-SPEC.md` 는 참고용 일반 원리이며, 이 저장소의 정본 규칙은
> `STT-PIPELINE-SPEC.md` + 본 문서 + `AUTORESEARCH.md` 세 문서에 둔다.

---

## 0. 큰 그림 — 3 단계 분리

| 단계 | 무엇 | 가드레일 | 누가 |
|------|------|----------|------|
| **Phase 1 — Harness 구축** | 골격·환경·judge·baseline·σ 측정 | **OFF** (자유롭게 수정) | 사람 |
| **Phase 2 — 평가 인프라** | `analyze_run.py`·`evaluate_holdout.py`·REPORT 템플릿 | OFF | 사람 |
| **Phase 3 — Autoresearch 실행 + 분석** | autoresearch 가 transcribe.py 진화 + 잡 종료 후 Phase 2 도구로 평가 | **ON** — 4 layer: ① holdout chmod 000, ② verify.sh 정적 grep + 수치 가드, ③ 우리 `.claude/` PreToolUse 훅, ④ `.ckignore` 읽기 차단 | 에이전트 + 사람(분석) |

**왜 분리**:
- Phase 1 가드레일 켜면 셋업 자체가 막힘 (judge 작성 중 holdout 접근, 초기 스텁이 가드 위반).
- 평가 인프라(Phase 2) 를 잡 *전* 에 만들지 않으면, 잡 후 관측한 결과에 분석을 reverse-fit 할 위험.
- Phase 3 진입 시점에 가드레일 *일괄 활성화* 후 에이전트에 넘긴다.

**왜 4 layer 가드** (AUTORESEARCH.md §6·§7 참조):
- autoresearch 의 `Scope` 는 prompt-only — Edit/Write sandbox 가 아니다.
- autoresearch 의 9가지 자체 훅은 일반 안전 (privacy/danger/context bloat) 만
  다루고 본 프로젝트의 scope 는 보호하지 않으며, `AR_DISABLE_*` ENV 로 우회 가능.
- 따라서 holdout 보호는 OS chmod, workspace 내 정적 위반은 verify, 그 외
  영역 (judge/ frozen/ baseline/ assets/ 보호 scripts) 의 *편집* 차단은 우리
  `.claude/` PreToolUse 훅이 책임진다.

---

## 1. 디렉토리 레이아웃

```
aig/
├── docs/
│   ├── STT-PIPELINE-SPEC.md     # 도메인 명세 (변경 금지)
│   ├── DESIGN.md                # 본 문서
│   ├── AUTORESEARCH.md          # autoresearch 정본 (정체·동작·가드 함의)
│   ├── PHASE1-PLAN.md           # Harness 구축 절차
│   ├── PHASE2-PLAN.md           # 평가 인프라 구축 절차
│   ├── PHASE3-PLAN.md           # autoresearch 실행 + 분석 절차
│   ├── PHASE3-LOOP.md           # Phase 3 loop / agent architecture Mermaid
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
│   └── evaluate.py              # entry point: run → score_report.json + diagnosis_report.json
├── scripts/
│   ├── verify.sh                # autoresearch Verify 명령. Phase 1·2 미니멀 본문이 활성.
│   ├── verify.sh.alt            # Phase 3 가드 본문 (정적 grep + verify_check 호출)
│   ├── verify_check.py          # Phase 3 수치 가드 (산술/catastrophic/runtime/quality)
│   ├── swap_verify.sh           # *사람 전용* — verify.sh ↔ verify.sh.alt 1:1 swap
│   ├── swap_claude.sh           # *사람 전용* — .claude ↔ .claude.alt 1:1 swap (Phase 3 후속)
│   ├── build_audio_profile.py   # 0715 audio-only profile 생성 (Silero VAD)
│   ├── measure_baseline.py      # faster-whisper 1회 측정
│   ├── measure_sigma.py         # 대표 파일 3회 반복 → σ proxy
│   ├── seal_holdout.sh          # Phase 3 진입 시 holdout chmod
│   ├── analyze_run.py           # Phase 2 산출 — runs/ 종합 분석 → REPORT.md
│   └── evaluate_holdout.py      # Phase 2 산출 — holdout 1 회 평가 + overfit 진단
├── baseline/
│   ├── target_cer.json          # 봉인된 oracle (Phase 1 종료 시 생성)
│   └── noise_floor.json         # σ (Phase 1 종료 시 생성. representative-file proxy)
├── assets/
│   └── audio_profile/
│       └── AIG_녹취반출_20250715.json  # 0715 audio-only 특성 (Phase 1 산출).
│                                        # 0813 (holdout) 는 Phase 3 *전* 생성 X
├── runs/
│   ├── <hyp_id>/                # autoresearch iteration별 산출물
│   │   ├── score_report.json
│   │   ├── per_file.jsonl
│   │   ├── diagnosis_report.json # LLM 추론용 11파일 summary + focus 최대 2개
│   │   └── _telemetry/
│   │       ├── <file_id>.jsonl   # 정본 segment telemetry (optional)
│   │       └── <file_id>.srt     # JSONL 에서 일방향 변환된 사람용 view
│   └── _summary/                # Phase 3 종료 시 산출 (REPORT.md, HOLDOUT.md)
├── .claude/                    # Phase 1·2 동안 부재 또는 가드 OFF 본문. Phase 3
│                                # 진입 시 사람이 `scripts/swap_claude.sh` 로 활성화
│   ├── settings.json           # tool permissions allowlist (Edit/Write 대상 제한)
│   ├── hooks/                  # PreToolUse 훅 — judge/, frozen/, baseline/,
│   │                            # assets/, 보호 scripts 편집 거부 (ENV 우회 불가)
│   └── ...
├── .claude.alt/                # Phase 3 본문 보관 (swap 대상)
├── .ckignore                   # autoresearch scout-block 읽기 차단 확장
├── tests/
├── pyproject.toml or requirements.txt
└── README.md
```

> `.claude/` / `.claude.alt/` / `.ckignore` / `swap_claude.sh` 는 Phase 3 진입
> 가드 작업의 후속 산출이다. 본 문서 갱신 시점에 *미작성* — `phase3` 브랜치에서
> 추가 예정. PHASE3-PLAN §1.2 참조.

---

## 2. Phase 1 — Harness 구축

### 2.1 환경

| 항목 | 값 |
|------|------|
| Python | 3.10+ |
| Runtime | CUDA + float16 (로컬 GPU) |
| 주요 의존성 | `ctranslate2`, `faster-whisper` (baseline 측정용), `transformers`, `librosa`, `soundfile`, `numpy`, `rapidfuzz`, `huggingface_hub`, `silero-vad`, `pytest` |
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
1. `pairing` 으로 11 페어 로드
2. 각 wav → `librosa.load(sr=16000, mono=True)` → telemetry env 설정 → `transcribe(audio, sr)` 호출
3. per-file CER + 가드 산출 → `score_report.json` 작성
4. 11파일 전체의 profile summary 와 focus file 최대 2개를 결합해
   `diagnosis_report.json` 작성
5. **마지막 줄에 `corpus_cer` 한 숫자 print** (autoresearch Verify 가 파싱)

`transcribe(audio, sr) -> str` 계약은 유지한다. pipeline 이 coverage 를 보고하고 싶으면
judge 가 설정한 `ASR_TELEMETRY_DIR`, `ASR_TELEMETRY_FILE_ID` 를 사용해
`runs/<hyp_id>/_telemetry/<file_id>.jsonl` sidecar 를 쓴다. judge 는 있으면 읽고, 없으면
coverage guard 를 skip 한다.

`diagnosis_report.json` 은 에이전트 추론용이다. raw `assets/audio_profile/*.json` 전체를
직접 노출하지 않고, 11파일 전체의 summary 만 포함한다. guard 위반·worst CER·baseline
대비 악화 기준으로 선택된 focus file 최대 2개는 우선순위 표시일 뿐이다.
`speech_segments` 원본 start/end 리스트는 구체적인 chunking 힌트가 되므로 diagnosis 에
넣지 않고, segment 개수·발화 길이 분위수·무음 gap 분위수 같은 요약만 제공한다.

### 2.6 Frozen layer (`frozen/asr_backend.py`)

backend 봉인. workspace 는 `load()` / `generate()` / `to_storage_view()` 세 helper
만 호출하며, 모델·디바이스·precision 은 이 layer 가 hard-code. workspace 가
`ctranslate2` / `transformers` 를 직접 import 할 필요가 없도록 한다. Phase 3 진입
시 편집 금지.

```python
# frozen/asr_backend.py
def load() -> tuple[Whisper, WhisperProcessor]: ...     # 모델 캐시·고정 로드
def generate(features, prompts, **decoding_kwargs): ... # CT2 generate passthrough
def to_storage_view(np_array): ...                      # numpy → ctranslate2.StorageView wrap
```

- 봉인: 모델 이름, 변환 캐시 경로, device, compute_type
- workspace 자유: decoding_kwargs (beam, temperature, fallback, sampling 등 전부)
- 추가 보호: Phase 3 verify 가 workspace 에 `import ctranslate2` / `import transformers` / `from_pretrained` / `Whisper(` 중 어느 패턴이라도 출현 시 fail (PHASE3 §1.2)

### 2.7 초기 transcribe 스텁 (`workspace/transcribe.py`)

가장 단순한 호출 — frozen helper 3 개만 사용, chunking 없이 1 회.
30 초 초과 long-form 은 깨질 거고, **그게 autoresearch 가 풀어야 할 출발점**.

```python
from frozen.asr_backend import load, generate, to_storage_view

def transcribe(audio: np.ndarray, sr: int) -> str:
    model, proc = load()
    # feature 추출 → to_storage_view → prompt 구성 → generate → decode → str
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

`faster-whisper` 로 0715 11 페어 1회 transcribe → 동일 judge 로 점수 산출 →
`baseline/target_cer.json` 작성. `target_cer` 는 사람이 정한 *최종 목표*
(현재 `0.10`) 로 박히고, faster-whisper 가 실제로 낸 점수는 `baseline_cer`
필드에 별도로 저장된다 — 둘이 갖는 의미가 다르기 때문에 분리한다
(`STT-PIPELINE-SPEC.md §7`).

```json
{
  "target_cer": 0.10,
  "baseline_cer": 0.0XXX,
  "macro_cer": 0.0XXX,
  "num_files": 11,
  "num_files_scored": 11,
  "batches": ["AIG_녹취반출_20250715"],
  "total_audio_s": ...,
  "total_inference_time_s": ...,
  "runtime_s_per_audio_min": ...,
  "guard_baseline": {
    "empty_output_rate": ...,
    "length_ratio": {"mean": ..., "p05": ..., "p95": ...},
    "repeated_text_rate": ...,
    "audio_coverage_rate": null,
    "hallucination_hit_rate": ...,
    "hallucination_hits_total": ...
  },
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

이후 **재실행 금지** (결정론 보장). `baseline_cer` 는 한 번 봉인되면 품질
참조값으로만 쓰고, `target_cer` 는 사람이 의도해서 바꾸지 않는 한 그대로 둔다.

### 2.10 σ 측정 (`scripts/measure_sigma.py`) — representative-file proxy

비용 절감을 위해 corpus 전체 11 파일 3 회 대신 **대표 파일 1 개 3 회** 로 σ proxy
산출. SPEC §6.1 의 운영 옵션을 따른다.

**대표 파일 선정**: 0715 eval 페어 중 `audio_s` 최장 _l.wav. tie-break = path
lexical sort. 한 번 결정되면 noise_floor.json 에 박혀 잡 동안 고정.

```json
{
  "scope": "representative_file_proxy",
  "method": "longest _l.wav by audio_s, 3 runs, lexical tie-break",
  "representative_file": "data/raw/wav/AIG_녹취반출_20250715/<...>_l.wav",
  "representative_audio_s": ...,
  "samples": [0.XXX, 0.XXX, 0.XXX],
  "sigma": 0.00XX,
  "applies_to": "corpus_cer Δ threshold",
  "measured_against": "initial transcribe stub",
  "measured_at": "2026-05-28T..."
}
```

> 주의 1: 이 σ 는 corpus_cer 의 진짜 노이즈가 *아님* — 대표 파일 cer 의 노이즈 proxy.
> 의사결정 임계 (`Δcer ≥ 2σ`) 에 *근사* 로 차용. 정확한 corpus σ 는 비용 큼.
>
> 주의 2: 스텁이 *돌긴 돌아야* σ 측정 가능. σ ≈ 0 이거나 스텁이 깨지면 σ 측정은
> 첫 정상 가설 이후로 미룬다 (deferred 정책).

### 2.11 Phase 1 완료 기준 (Definition of Done)

- [ ] 데이터 페어링 코드가 0715 11 페어 정확히 매칭
- [ ] judge 가 스텁 transcribe 에 대해 score_report.json 산출
- [ ] judge 가 `diagnosis_report.json` 산출 (per_file_diagnosis 11개, focus file 최대 2개, raw `speech_segments` 미포함)
- [ ] `scripts/verify.sh` 실행 시 corpus_cer 숫자가 마지막 줄에 출력
- [ ] `assets/audio_profile/AIG_녹취반출_20250715.json` 생성 (0715 only — 0813 미생성)
- [ ] `baseline/target_cer.json` 생성 + 봉인 (재실행 금지 명시)
- [ ] `baseline/target_cer.json` 에 versions/model/decoding_params/hardware/guard_baseline 기록
- [ ] `baseline/noise_floor.json` 생성 (σ 측정 완료)
- [ ] normalize/pairing/metrics/evaluate smoke 테스트 통과
- [ ] 사람이 수동으로 verify.sh 1 회 돌려서 cer 숫자 확인
- [ ] holdout 배치명은 `workspace/`, `judge/`, prompt, 운영 wrapper 를 제외한 `scripts/` 에서
      참조하지 않음 (docs/README/AGENTS/CLAUDE 와 `scripts/seal_holdout.sh` 는 예외)

---

## 3. Phase 2 — 평가 인프라

상세는 [`PHASE2-PLAN.md`](PHASE2-PLAN.md). 정본으로 둔다.

**무엇을 분석하는가**: 잡 전체 (25 iter) 종료 후 *루프 진행 자체* 를 검증.
"최종 corpus_cer 가 좋아졌나" 만 보면 어떻게·어디서 깨졌는지 모르므로, 다음 질문에
답하는 도구를 미리 만든다 — 한쪽 방향 쏠림 / 큰 개선의 집중 vs 누적 / 가드 과잉
사살 / agent 의도-결과 일치 / 한두 파일이 점수 끌어옴 / holdout overfit. PHASE2-PLAN
§0 참조.

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
- 진입 직전 일괄 활성화 (사람 수동, 1줄씩): `swap_verify.sh` → `swap_claude.sh`
  → autoresearch 본체 설치 확인 → `seal_holdout.sh` → 사전 smoke. PHASE3-PLAN §1.
- `workspace/transcribe.py` 만 scope. **단 autoresearch 의 Scope 는 prompt-only
  이므로** (AUTORESEARCH.md §6) 실제 scope 강제는 우리 `.claude/` PreToolUse
  훅 + verify 정적 grep + chmod 의 4 layer 가드가 책임.
- primary metric 은 `corpus_cer`. autoresearch 는 verify 마지막 줄 숫자를 읽는다.
- 결정 = autoresearch (keep/revert). commit 이 verify *전* 일어남 — 작업 브랜치
  (`phase3`) 에서 운영. hard-fail 은 무효 후보만 즉시 ROLLBACK.
- 잡 종료 후 Phase 2 도구로 평가 — REPORT.md + HOLDOUT.md. autoresearch 자체
  TSV (`autoresearch/<sub>-<YYMMDD>-<HHMM>/*.tsv`) 는 보조 참조용일 뿐 분석 정본 아님.

---

## 5. 열려 있는 질문 (셋업 시 확인)

- GPU 사양·메모리: large-v3-turbo float16 + faster-whisper 동시 적재 가능한지
- Python 버전, ctranslate2 버전 호환성
- 데이터 실제 절대경로 — `ASR_RAW_DATA_ROOT` 설정 여부
- autoresearch 의 노이즈 σ 임계 자체 지원 여부 (PHASE3 §8 / AUTORESEARCH.md §11)
- autoresearch 본체 설치 위치 (글로벌 `~/.claude/` vs 프로젝트 `.claude/`) 와
  우리 프로젝트 `.claude/` 자산의 공존 (PHASE3 §1.2)
- 25 iter 총 소요 시간 추정 (per-iter verify 시간 측정 후)
- autoresearch 의 `experiment:` 커밋 누적 정도 — 잡 종료 후 squash 정책 필요 여부

**결정됨** (이전 열린 항목에서 옮김):
- autoresearch 의 파일 접근 권한 제어 → AUTORESEARCH.md §6 으로 해소. Sandbox
  없음. 우리 `.claude/` PreToolUse 훅 + verify 정적 grep + OS chmod 의 4 layer
  가드로 강제 (PHASE3 §3 권한 모델 결론).

---

## 6. 안티 패턴 (해선 안 되는 것)

- Phase 1 단계에서 가드레일을 미리 활성화 (코드 작성 막힘)
- Phase 3 에서 baseline guard/time 분포 측정 *전에* quality budget 을 확정
- 평가 도구를 Phase 3 *후* 에 만듦 (결과에 분석을 맞추는 reverse-fit)
- baseline/target_cer.json 을 잡 도중 갱신 (결정론 깨짐)
- σ 를 단일 측정으로 산출 (최소 3회)
- holdout 을 `workspace/`, `judge/`, prompt 에서 *언급* (실수로 참조 가능)
- judge 와 transcribe 가 같은 정규화 모듈을 import 하지 않고 각자 구현
- 8 개 축을 단일 점수로 환원해 자동 판정 — 사람 판단 항목은 사람이 채움
- autoresearch 의 `Scope` 입력 또는 9가지 자체 훅만 믿고 우리 `.claude/`
  PreToolUse 가드를 생략 (Scope 는 prompt 일 뿐, 자체 훅은 ENV 우회 가능)
- `AR_DISABLE_*` ENV 로 autoresearch 자체 훅을 끄고 운영
- `scripts/swap_verify.sh` 또는 `scripts/swap_claude.sh` 를 에이전트(Claude /
  autoresearch / 보조) 가 호출 — 둘 다 사람 전용
