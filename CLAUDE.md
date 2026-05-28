# CLAUDE.md

기본 행동 규약은 [`AGENTS.md`](AGENTS.md) — 모든 에이전트 공통. 본 파일은 Claude
Code 세션 특화 보충만.

---

## 1. Phase 2 호출

`/autoresearch` 호출 형태·운영 절차는 [`docs/PHASE2-PLAN.md §2`](docs/PHASE2-PLAN.md).
산출물 위치는 PHASE2-PLAN §5.

---

## 2. 채팅 응답 스타일

- 채팅 응답은 **짧고 간결하게**. 장황한 설명·중복 요약·불필요한 머리말 금지.
- 한 줄로 끝낼 수 있으면 한 줄로. 결정·결과·다음 한 단계만.
- **이 규칙은 채팅 응답 한정**. 코드·문서(`docs/`, `README.md`, 코드 주석 등) 작성
  시는 명세대로 충실히. 본 규칙을 코드·문서에 끌어다 적용하지 말 것.

---

## 3. 슬래시 커맨드

- `/autoresearch` — 메인 루프
- `/autoresearch:plan` — 4 종 입력 검증·구체화 (본 루프 진입 전 권장)
- `/autoresearch:evals` — 과거 runs 분석

---

## 4. 디버깅 시

- 가설이 깨지면 `runs/<hyp_id>/per_file.jsonl` per-file telemetry 부터 본다.
- `judge/` 본문 의심되면 *제안만* — 사람 확인 없이 편집 X (평가자 보호).
- holdout (0813) 은 어떤 디버깅 단계에서도 건드리지 않는다.
