# SSOT — 문서 정본 지도

> 목적: 문서가 늘어나도 같은 정책이 여러 곳에서 다르게 설명되지 않도록,
> 주제별 정본 위치와 상태 문서의 역할을 고정한다.

---

## 1. 정본 원칙

- 상세 정의는 한 곳에만 둔다.
- 다른 문서는 요약과 링크만 둔다.
- 계획 문서에는 진행 체크박스를 두지 않는다.
- 상태 문서에는 절차 본문을 복사하지 않고 계획 문서의 섹션만 참조한다.
- 실험 결과 수치는 `runs/` 산출물을 우선한다.

---

## 2. 주제별 정본

| 주제 | 정본 | 비고 |
|------|------|------|
| 문제 정의, 데이터, 정규화, metric, 금지사항 | [`STT-PIPELINE-SPEC.md`](STT-PIPELINE-SPEC.md) | 도메인 정본 |
| 시스템 구조, 디렉토리 책임, phase 경계 | [`DESIGN.md`](DESIGN.md) | 구조 정본 |
| Phase 1 구축 절차 | [`PHASE1-PLAN.md`](PHASE1-PLAN.md) | 완료 상태는 status 스냅샷 |
| Phase 2 평가 인프라 절차 | [`PHASE2-PLAN.md`](PHASE2-PLAN.md) | 완료 상태는 status 스냅샷 |
| Phase 3 자체 harness 운영 절차 | [`PHASE3-PLAN.md`](PHASE3-PLAN.md) | 체크박스 금지 |
| Phase 3 DoD / 현재 상태 | [`PHASE3-STATUS.md`](PHASE3-STATUS.md) | PLAN 섹션 참조만 |
| Phase 3 구조 그림 | [`PHASE3-LOOP.md`](PHASE3-LOOP.md) | 보조 문서, 정본 아님 |
| 세션 종료 인계 메모 | [`status/`](status/) | 날짜별 스냅샷 |
| 실제 iteration 로그 | [`../runs/_summary/HISTORY.md`](../runs/_summary/HISTORY.md) | 실험 기록 정본 |
| 종료 후 종합 리포트 | `../runs/_summary/REPORT.md`, `../runs/_summary/HOLDOUT.md` | 생성 산출물 |
| autoresearch 조사 기록 | [`AUTORESEARCH.md`](AUTORESEARCH.md) | historical/deprecated |
| 일반 self-evolve 참고 | [`SELF-EVOLVE-HARNESS-SPEC.md`](SELF-EVOLVE-HARNESS-SPEC.md) | 참고용, 정본 아님 |

---

## 3. 코드 책임 지도

| 영역 | 책임 |
|------|------|
| `harness/` | Phase 3 controller 로직: guard, policy, state, history, runner |
| `scripts/` | 사람이 실행하는 thin CLI 또는 일회성 운영 명령 |
| `judge/` | 평가 산출: score, per-file, diagnosis |
| `frozen/` | 고정 ASR backend |
| `workspace/` | 후보 파이프라인 표면 (`transcribe(audio, sr) -> str`) |
| `baseline/` | 봉인된 target/baseline/noise floor |
| `runs/<hyp_id>/` | iteration별 평가 산출물 |
| `runs/_summary/` | harness 전용 HISTORY/state/REPORT/HOLDOUT |

---

## 4. 충돌 시 우선순위

1. `STT-PIPELINE-SPEC.md` — 문제·평가·금지사항
2. `DESIGN.md` — 시스템 구조
3. `PHASE*-PLAN.md` — phase별 절차
4. `PHASE3-STATUS.md` / `docs/status/*` — 현재 상태
5. `README.md`, `AGENTS.md`, `CLAUDE.md` — 요약·진입점

문서와 코드가 충돌하면, 정책 충돌은 정본 문서에서 먼저 고치고 구현을 맞춘다.
실험 결과 충돌은 `score_report.json`과 `runs/_summary/HISTORY.md`를 우선한다.
