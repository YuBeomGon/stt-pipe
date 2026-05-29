# CLAUDE.md

기본 행동 규약은 [`AGENTS.md`](AGENTS.md) — 모든 에이전트 공통. 본 파일은 Claude
Code 세션 특화 보충만.

---

## 1. Phase 3 호출

Phase 3 운영 절차는 [`docs/PHASE3-PLAN.md`](docs/PHASE3-PLAN.md)를 따른다.
진행 상태는 [`docs/PHASE3-STATUS.md`](docs/PHASE3-STATUS.md)에만 기록한다.

---

## 2. 채팅 응답 스타일

- 채팅 응답은 **짧고 간결하게**. 장황한 설명·중복 요약·불필요한 머리말 금지.
- 한 줄로 끝낼 수 있으면 한 줄로. 결정·결과·다음 한 단계만.
- **이 규칙은 채팅 응답 한정**. 코드·문서(`docs/`, `README.md`, 코드 주석 등) 작성
  시는 명세대로 충실히. 본 규칙을 코드·문서에 끌어다 적용하지 말 것.

---

## 3. 실행 진입점

- `harness/` — Phase 3 controller 로직
- `scripts/` — 사람이 실행하는 thin CLI 또는 운영 명령
- `scripts/verify.sh` — 현재 후보 평가
- `scripts/evolve.py --candidate-cmd "claude -p"` — terminal-driven Phase 3 loop
- `scripts/analyze_run.py` — 종료 후 REPORT 생성
- `scripts/evaluate_holdout.py --unseal` — 종료 후 holdout 1회 평가

---

## 4. 디버깅 시

- 가설이 깨지면 `runs/<hyp_id>/diagnosis_report.json` 의 focus file 요약을 먼저 보고,
  필요하면 `per_file.jsonl` / `_telemetry/` 로 내려간다.
- `judge/` 본문 의심되면 *제안만* — 사람 확인 없이 편집 X (평가자 보호).
- holdout (0813) 은 어떤 디버깅 단계에서도 건드리지 않는다.
