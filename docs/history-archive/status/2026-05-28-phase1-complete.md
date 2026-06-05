# STATUS — 진행 상황 메모

> 다음 세션 (집/다른 머신) 진입 시 컨텍스트 회복용. 사용 데이터·모델 캐시는
> 머신에 따라 다르므로 코드/문서/측정 산출물만 본 저장소에 들어 있다.
> 갱신 시점: **2026-05-28** (Phase 1 완료 직후)

---

## 1. 한 줄 요약

Phase 1 (Harness + baseline + σ proxy) **완료**. Phase 2 (평가 인프라) 시작 가능.
Phase 3 (autoresearch loop) 은 Phase 2 DoD 통과 후.

---

## 2. 측정 산출물 스냅샷

### 2.1 baseline (`baseline/target_cer.json`)

| 항목 | 값 |
|------|------|
| `target_cer` (수동 목표) | **0.10** |
| `baseline_cer` (faster-whisper) | **0.4320** |
| `macro_cer` | 0.4685 |
| `num_files` / `num_files_scored` | 11 / 11 |
| `total_audio_s` | 18 041.5 s (≈ 5.01 h) |
| `total_inference_time_s` | 152.3 s |
| `runtime_s_per_audio_min` | 0.51 (RTF ≈ 0.0085) |
| `guard_baseline.empty_output_rate` | 0.00 |
| `guard_baseline.length_ratio` | mean 0.60 / p05 0.36 / p95 0.80 |
| `guard_baseline.repeated_text_rate` | 0.091 |
| `guard_baseline.hallucination_hit_rate` | 0.364 (4/11) / hits_total 12 |
| `guard_baseline.audio_coverage_rate` | null (telemetry 미구현) |

decoding params: `beam_size=5, language=ko, task=transcribe, vad_filter=True,
without_timestamps=True, condition_on_previous_text=False`.

해석:
- 출력이 reference 대비 평균 60% 길이 — 모델이 누락하는 양이 큼
- 4/11 파일에 한국어 Whisper 알려진 환각 패턴 매치 (`시청해주셔서 감사합니다` 등)
- 도메인 (보험 콜센터 8 kHz → 16 kHz 업샘플 통화) 자체가 어려움
- baseline 은 **off-the-shelf 점수 그대로 봉인** — autoresearch 의 거리감 측정용

### 2.2 σ proxy (`baseline/noise_floor.json`)

| 항목 | 값 |
|------|------|
| `sigma` | **0.0** |
| `samples` | [0.9954, 0.9954, 0.9954] |
| `representative_file` | `02_4038_0106613XXXX_..._12_06_50_l.wav` (2 596.6 s, 최장) |
| `is_provisional` | **true** |
| `note` | PROVISIONAL — 30 s truncation + greedy decoding stub 으로 측정 |

PHASE1-PLAN §9.3 케이스. Phase 3 첫 정상 가설 이후 재측정 필요. 그때까지
autoresearch 는 절대 Δcer threshold (제안값 0.01) 로 keep/revert 판정.

### 2.3 audio profile (`assets/audio_profile/AIG_녹취반출_20250715.json`)

11 파일, silero-vad 6.2.1 + librosa RMS aggregates. `judge/evaluate.py` 와
`scripts/analyze_run.py` 만 원본을 읽음. workspace 에는 `diagnosis_report.json`
의 summary 만 노출 (raw `speech_segments` 미포함).

---

## 3. 한 것 (Phase 1 Steps 1 – 10)

| Step | 상태 | 산출물 |
|------|------|--------|
| 0 사전 점검 | ✅ | nvidia-smi, 데이터 11+1 페어 확인, `data/raw` symlink |
| 1 골격 + 의존성 | ✅ | `requirements.txt`, dir tree |
| 2 페어링 + 정규화 | ✅ | `judge/pairing.py`, `judge/normalize.py` |
| 3 메트릭 + diagnosis | ✅ | `judge/metrics.py`, `judge/diagnosis.py` |
| 4 evaluate + verify | ✅ | `judge/evaluate.py`, `scripts/verify.sh` |
| 5 frozen layer | ✅ | `frozen/asr_backend.py` (load / generate / to_storage_view) |
| 6 workspace stub | ✅ | `workspace/transcribe.py` (30 s 한 번, greedy) |
| 7 audio profile | ✅ | silero-vad 기반, 11 파일 |
| 8 baseline | ✅ | `baseline/target_cer.json` |
| 9 σ | ✅ (provisional) | `baseline/noise_floor.json` |
| 10 DoD | ✅ | 본 STATUS + 17 pytest |
| 보조 | ✅ | `scripts/seal_holdout.sh` (Phase 3 진입 시 사용) |

### 3.1 진행 중에 정해진 결정

- **degenerate label 자동 스킵**: `01_8088_010XXXX1216_..._10_25_39_l.txt` 가 turn
  번호만 있는 57 초 무음 파일이라 `pair_batch(skip_empty_labels=True)` 기본값에서
  제외. 0715 의 실효 페어 수는 12 → **11**.
- **target_cer / baseline_cer 분리**: `target_cer` 는 사람이 정한 수동 목표
  (0.10), `baseline_cer` 는 측정값 (0.432). Phase 3 success 기준은 target_cer.
- **σ provisional**: stub 결정론 + 30 s 절단으로 σ=0. Phase 3 진입 후 첫 정상
  가설에 대해 재측정 필요.

---

## 4. 다음 할 것

### 4.1 Phase 2 — 평가 인프라 (실행 전 작성)

목적: Phase 3 결과를 사후 분석하기 위한 도구를 *미리* 만들어 두기 (reverse-fit 방지).

| To do | 위치 | 비고 |
|-------|------|------|
| `scripts/analyze_run.py` | scripts/ | 8 축 (A~H) REPORT 자동 산출 |
| `scripts/evaluate_holdout.py` | scripts/ | `--unseal` + `JOB_DONE.lock` 검사. 호출 1 회 |
| `docs/templates/REPORT.md` | docs/templates/ | 사람 판단 칸 빈칸으로 |
| 8 축 정의 확정 | docs/PHASE2-PLAN.md | 이미 §1 에 있음. 산출 가능/사람 판단 구분만 다시 점검 |

자세한 내용: [`docs/PHASE2-PLAN.md`](PHASE2-PLAN.md).

### 4.2 Phase 3 — autoresearch 실행 + 분석

선행 조건: Phase 2 DoD 통과. 진입 직전 액션:

1. `bash scripts/seal_holdout.sh` (0813 chmod 000)
2. `scripts/verify.sh` 에 정적 backend / profile 가드, runtime cap, quality budget 추가 (현재 verify.sh 는 Phase 1 용으로 가드 없음)
3. `/autoresearch:plan` 으로 4 종 입력 (Goal / Scope / Metric / Verify) 검증
4. `/autoresearch` 본 루프 진입 (제안 iter 25)

루프 구조: [`docs/PHASE3-LOOP.md`](PHASE3-LOOP.md). 운영 정본:
[`docs/PHASE3-PLAN.md`](PHASE3-PLAN.md).

### 4.3 σ 재측정 (Phase 3 부속 작업)

Phase 3 첫 정상 가설 (cer < 0.9 정도) 직후 `scripts/measure_sigma.py` 다시 돌려서
`baseline/noise_floor.json` 갱신. 그 전까지는 absolute Δcer threshold (≈ 0.01)
로 keep/revert.

### 4.4 열린 항목 (PHASE3-PLAN §8)

- autoresearch 의 `Δcer ≥ 2σ` 노이즈 임계 자체 지원 여부 확인
- autoresearch 의 파일 접근 권한 제어 메커니즘 확인
- 25 iter 총 소요 시간 (1 iter ≈ baseline decode 시간 + verify overhead 추정)
- quality budget 허용폭 (baseline guard 분포 측정값 기준 확정)

---

## 5. 머신 셋업 메모 (집에서 이어 작업할 때)

### 5.1 의존성

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

설치된 버전과 `requirements.txt` 에 적힌 버전이 살짝 다를 수 있다 (silero-vad 는
실제로 6.2.1 설치됨, requirements 에는 5.1 명기). 동작에는 문제 없음.

### 5.2 데이터 경로

집 머신에 데이터가 없을 가능성이 큼. 데이터가 있다면:

```bash
export ASR_RAW_DATA_ROOT=/path/to/aig-audio-3/data/raw
# 또는
ln -s /path/to/aig-audio-3/data/raw data/raw
```

데이터가 없으면:
- 코드/문서 편집·`pytest`·docs 작업까지는 가능
- `pair_batch`·`evaluate`·`measure_*` 는 모두 데이터 필요

### 5.3 모델 캐시

- HF cache (`~/.cache/huggingface/`) 첫 호출 시 `openai/whisper-large-v3-turbo`
  자동 다운로드 (~3 GB)
- CT2 변환 캐시 (`.cache/ct2_models/whisper-large-v3-turbo/`) 는 `frozen.load()`
  첫 호출 시 자동 생성 (~3 GB, 변환 시간 수 분)
- `.cache/` 는 gitignore 됨. 캐시는 머신마다 재생성

### 5.4 GPU

CUDA + float16 14 GB+ 필요. 4080 SUPER 16 GB 에서는 baseline / sigma / Phase 3
inference 모두 동작 확인.

---

## 6. 디버깅 진입점

- 가설이 깨지면: `runs/<hyp_id>/diagnosis_report.json` 의 `focus_files` → `per_file.jsonl` → `_telemetry/`
- baseline 점수 의심: `baseline/target_cer.json` 의 `per_file[]` 항목별 cer 확인. faster-whisper hyp 텍스트는 저장 안 됨 (필요하면 measure_baseline 에 옵션 추가)
- judge/ 본문 의심: *제안만*, 사람 확인 없이 편집 X (평가자 보호)
- holdout (0813) 은 어떤 단계에서도 건드리지 않음

---

## 7. 참고 — 알려진 risk / quirk

- faster-whisper baseline `length_ratio mean 0.60` 은 *모델이 짧게 끊는다* 는
  신호. condition_on_previous_text=False 와 한국어 콜센터 long-form 의 조합으로
  중간 환각 → 일찍 끊김 패턴이 의심됨
- hallucination_hit_rate 36% — YouTube 자막 패턴 (시청해주셔서 감사합니다 등) 이
  baseline 자체에서도 자주 나옴. 우리 파이프라인은 이 패턴을 절대 만들지 말아야 함
- repeated_text_rate 9% (1/11 파일) — 1 개 파일이 같은 패턴 반복
- σ provisional 임을 잊지 말 것 (`is_provisional: true`)
