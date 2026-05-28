# CLAUDE.md

기본 행동 규약은 `AGENTS.md` 를 따른다 (모든 에이전트 공통). 본 파일은 Claude Code
세션에서만 추가로 알아야 할 사항.

---

## 1. AGENTS.md 우선

먼저 `AGENTS.md` 를 읽는다. 본 파일은 그 위의 *Claude Code 특화 보충* 만 담는다.

---

## 2. Phase 2 호출 형태 — `/autoresearch`

Phase 2 진입 시 다음 형태로 호출:

```
/autoresearch
Goal: workspace/transcribe.py 의 transcribe(audio, sr) 를 진화시켜 0715 14 페어 corpus_cer 을 baseline/target_cer.json 의 target_cer 이하로 낮춘다. backend·model 변경 금지 (docs/STT-PIPELINE-SPEC.md §2, §11).
Scope: workspace/transcribe.py
Metric: corpus_cer (lower is better)
Verify: bash scripts/verify.sh
Iterations: 25
```

세션 안에서 직접 입력. 외부 shell wrapper 형태 아님.

---

## 3. 매 iter 결과 위치

- workspace 변경: autoresearch 가 git 으로 commit / revert
- 측정 산출물: `runs/<hyp_id>/score_report.json` + `runs/<hyp_id>/per_file.jsonl`
- `score_report.json` 은 **읽기만**. 직접 편집 금지.

---

## 4. 디버깅 시 행동 규약

- 가설이 깨지면 `runs/<hyp_id>/per_file.jsonl` 의 per-file telemetry 부터 본다.
- judge/normalize 결과가 의심되면 작은 스니펫으로 재현 후 `judge/` 본문 변경 *제안만*
  — 사람 확인 없이 변경 X (평가자 본문 보호).
- holdout (0813) 은 어떤 디버깅 단계에서도 건드리지 않는다.

---

## 5. 채팅 응답 스타일

- 채팅 응답은 **짧고 간결하게**. 장황한 설명·중복 요약·불필요한 머리말 금지.
- 한 줄로 끝낼 수 있으면 한 줄로. 결정·결과·다음 한 단계만.
- **이 규칙은 채팅 응답 한정**. 코드·문서(`docs/`, `README.md`, 코드 주석 등) 작성 시는
  명세대로 충실히. 본 규칙을 코드·문서에 끌어다 적용하지 말 것.

---

## 6. 슬래시 커맨드

- `/autoresearch` — 메인 루프 (위 §2)
- `/autoresearch:plan` — 4종 입력 검증·구체화 (선택)
- `/autoresearch:evals` — 과거 runs 분석

`/autoresearch:plan` 을 한 번 돌려 입력을 검증한 뒤 본 루프 진입을 권장.
