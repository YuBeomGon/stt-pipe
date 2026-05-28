# AGENTS.md

이 저장소에서 작업하는 모든 AI 에이전트가 따라야 할 규약. 사람·LLM 도구 무관.

---

## 0. 한 줄 임무

한국어 보험 콜센터 음성에 대해, `ctranslate2 + whisper-large-v3-turbo` 위에
`transcribe(audio, sr) -> str` 파이프라인을 자동 진화시켜 corpus-level CER 을
faster-whisper baseline 이하로 낮춘다.

---

## 1. 무엇을 만지고 무엇을 만지지 않는가

| 영역 | 권한 |
|------|------|
| `workspace/transcribe.py` | **편집 OK — 유일한 표면** |
| `judge/` | **편집 금지** — 평가자 본문. 정규화·메트릭 규칙은 도메인 명세에 박혀 있음. |
| `baseline/` | **편집 금지** — `target_cer.json`, `noise_floor.json` 봉인됨. 재측정 금지. |
| `data/raw/.../AIG_녹취반출_20250715/` | **읽기만** — eval 데이터셋 |
| `data/raw/.../AIG_녹취반출_20250813/` | **접근 절대 금지** — holdout |
| `scripts/`, `docs/` | 사람 영역. 에이전트는 수정 X (Phase 2 에서 autoresearch 가 호출만 함) |
| `runs/` | iteration 산출물. 사람·에이전트 모두 *읽기* 만 (변경은 verify 가) |

holdout 이름·경로 참조 금지 범위는 `workspace/`, `judge/`, prompt, 그리고 운영 wrapper 를
제외한 `scripts/` 이다. 정본·운영 문서(`docs/`, `README.md`, `AGENTS.md`, `CLAUDE.md`)
와 `scripts/seal_holdout.sh` 는 명시적 안내를 위해 예외로 둔다.

---

## 2. 의사결정 기준

| 무엇 | 어디 |
|------|------|
| Primary metric | `corpus_cer` (lower is better) — 모든 keep/discard 의 기준 |
| Target | `baseline/target_cer.json` 의 `target_cer` 이하 → 성공 |
| 의미 있는 개선 | `Δcer ≥ 2σ` (σ = `baseline/noise_floor.json`) |
| 가드 위반 (Phase 2) | `hallucination_hit_rate`, `empty_output_rate`, `length_ratio`, `repeated_text_rate` 임계 초과 시 즉시 ROLLBACK. `audio_coverage_rate` 는 sidecar telemetry 가 있을 때만 hard gate 적용 |

판단의 근거는 항상 `runs/<hyp_id>/score_report.json` — 추측이나 자기 보고로 결정하지
말 것.

---

## 3. 정보 출처 (읽는 순서)

1. **`docs/STT-PIPELINE-SPEC.md`** — 도메인 명세. 모든 문제 정의·정규화·가드의 source of truth. **변경 금지**.
2. **`docs/DESIGN.md`** — 시스템 설계 (2 단계 구조, 디렉토리 레이아웃, 컴포넌트).
3. **`docs/PHASE1-PLAN.md`** — Harness 구축 절차.
4. **`docs/PHASE2-PLAN.md`** — autoresearch 자동화 절차 (존재 시).
5. **`README.md`** — 사람용 빠른 시작.
6. **`docs/SELF-EVOLVE-HARNESS-SPEC.md`** — 참고용 일반 원리. 이 저장소의 정본 규칙으로
   직접 승격하지 않는다.

명세 본문의 라벨 문장을 prompt/후처리에 직접 주입하지 말 것 (명세 §11).

---

## 4. 절대 금지 (`STT-PIPELINE-SPEC.md §11` 그대로)

- holdout (`AIG_녹취반출_20250813`) 접근
- eval label 본문을 prompt/후처리에 직접 주입
- 모델 fine-tuning
- backend 변경 (Whisper-large-v3-turbo + CT2 외)
- `[INAUDIBLE]` 토큰을 정답 측에서 의도적으로 활용

---

## 5. 단계 (Phase) 별 행동 규약

### Phase 1 — Harness 구축 (사람 주도)

- 가드레일 OFF. 자유롭게 코드 작성.
- 산출물: `judge/`, `scripts/`, 초기 `workspace/transcribe.py`, `baseline/*.json`
- 에이전트는 보조만 (지시 받은 부분 작성). 자동 루프 X.

### Phase 2 — autoresearch 자동화

- 가드레일 ON (holdout chmod, verify 가드 hard-fail).
- 에이전트가 `/autoresearch` 루프 안에서 `workspace/transcribe.py` 만 수정.
- 매 iter: 편집 → `bash scripts/verify.sh` → corpus_cer 출력 → autoresearch 가 keep/revert 결정.

---

## 6. 검증 흐름

```
bash scripts/verify.sh
```

마지막 줄에 `corpus_cer` 숫자 한 줄이 print 되어야 한다. 그 외 의사 결정 신호 없음.

가드 위반은 (Phase 2 부터) verify 가 exit 1 로 알린다 — 점수 무관 즉시 ROLLBACK.

---

## 7. Commit / 브랜치

Phase 2 의 commit / revert 는 autoresearch 가 처리. 에이전트가 명시적으로 git 명령
호출하지 말 것. Phase 1 에서 사람이 정상 커밋만.

---

## 8. 한 줄 요약

> **`workspace/transcribe.py` 한 파일만 만진다. 결정은 `runs/<hyp_id>/score_report.json` 의 `corpus_cer` 으로 한다. 명세는 `docs/STT-PIPELINE-SPEC.md`. 그 외는 모두 보조.**
