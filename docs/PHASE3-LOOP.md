# Phase 3 Loop — Harness Architecture

> 목적: 자체 `harness/` loop의 입력, 산출물, keep/reject 흐름을 한눈에 보기 위한
> 보조 문서. 운영 정본은 [`PHASE3-PLAN.md`](PHASE3-PLAN.md)이다.

---

## 1. 전체 루프

```mermaid
flowchart TD
    A[Phase 3 진입] --> B[holdout 봉인 확인]
    B --> C[protected 영역 확인]
    C --> D[harness runner 시작]

    D --> E[후보가 workspace/transcribe.py 수정]
    E --> F[정적 금지 패턴 검사]
    F --> G[judge.evaluate<br/>0715 11 files]

    G --> H[score_report.json<br/>CER/time/guards]
    G --> I[per_file.jsonl]
    G --> J[diagnosis_report.json]
    G --> K[_telemetry optional]

    H --> L[harness.guards]
    L --> M{Hard fail?}
    M -->|yes| R[REJECT / rollback]
    M -->|no| N[harness.policy]
    N --> O{Success?}
    O -->|yes| S[SUCCESS / JOB_DONE]
    O -->|no| P{Meaningful improvement?}
    P -->|yes| Q[KEEP / best 갱신]
    P -->|no| R

    Q --> T[HISTORY append]
    R --> T
    T --> U{iter 남음?}
    U -->|yes| E
    U -->|no| V[analyze_run.py]
    S --> V
    V --> W[evaluate_holdout.py --unseal]
```

---

## 2. Iteration 내부 시퀀스

```mermaid
sequenceDiagram
    participant H as harness.runner
    participant W as workspace/transcribe.py
    participant J as judge.evaluate
    participant G as harness.guards
    participant P as harness.policy
    participant R as runs/<hyp_id>

    H->>W: 후보 변경 적용
    H->>H: 정적 금지 패턴 검사
    H->>J: evaluate 0715 11 pairs
    loop each wav
        J->>W: transcribe(audio, sr)
        W-->>J: text
    end
    J->>R: score_report.json / per_file.jsonl / diagnosis_report.json
    H->>G: guard checks
    G-->>H: pass / fail + warnings
    H->>P: best, score, sigma 입력
    P-->>H: keep / reject / success
    H->>R: HISTORY append
```

---

## 3. 노출 모델

```mermaid
flowchart LR
    AP[assets/audio_profile raw] -->|read only| J[judge.evaluate]
    AP -->|read only after job| AN[scripts/analyze_run.py]
    AP -. direct read forbidden .-> W[workspace/transcribe.py]

    J --> D[diagnosis_report.json<br/>summary + focus]
    D --> H[harness / candidate context]
    PF[per_file.jsonl] --> H
    SR[score_report.json] --> H
    TEL[_telemetry optional] --> H
```

`speech_segments` 원본 start/end 리스트는 `diagnosis_report.json`에 넣지 않는다.
후보는 raw audio profile이 아니라 diagnosis summary와 자기 telemetry만 사용한다.

---

## 4. 판단 기준

| 층위 | 기준 | 역할 |
|------|------|------|
| Hard fail | backend/profile 직접참조, holdout 접근, 산술 불일치, evaluate 실패 | 무효 후보 reject |
| Final target | `corpus_cer <= target_cer` + baseline time budget | 성공 |
| Noise floor | `Δcer >= 2σ` 또는 provisional fallback | keep |
| Quality diagnostics | hallucination/repetition/length/coverage | warning 또는 hard fail |
