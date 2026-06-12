# phase3_015 — run 회고

## 개요

Gen7(refactor-harness) 구조의 첫 smoke 검증 run (7 iter). 검증 대상 하네스 기능:

- **metadata off-git**: `runs/` 전부 gitignore, decisions/state/portfolio 를 `runs/_archive/phase3_015_*.jsonl|json` 으로 기록
- **champion ref 분리** (`champion_ref: "champion"`) — HEAD 는 lineage head, 승격 코드는 별도 git ref
- **lineage set** (`--set-budget`): champion 보다 나쁜 explore 를 set seed 로 육성 (`lineage_advance` / `set:repair` / `set:refine` override)
- **portfolio_v1 스케줄러**: scheduled_mode vs chosen_mode override (`no_best`, `discovery_phase`, `set:*`), signature cooldown, portfolio slot (`global_best`/`family_best`/`metric_best`/`micro_bank`/`near_best`)
- **scope 가드** (tracked=git status + fs snapshot diff)

점수 자체보다 위 기계장치가 의도대로 도는지가 목적인 run.

## Iteration 기록

| iter | mode | 시도한 것 (한 줄) | CER | 판정 |
|---|---|---|---|---|
| 001 | explore (ovr `no_best`) | 30s 윈도우 분할 + 전 윈도우를 한 번의 batched `generate()` 로 long-form 디코드 | — (미채점: judge.evaluate 비정상 종료 — CUDA OOM) | reject (`verify_fail`) |
| 002 | repair (sched explore → `set:repair`) | iter_001 의 windowing 유지 + 윈도우별 순차 `generate()` 호출로 OOM 수리 | 0.411350 | keep — first valid candidate |
| 003 | explore (ovr `discovery_phase`) | `<|notimestamps|>` 제거 → timestamp 토큰으로 다음 윈도우 seek 조향 (family_002) | 0.175731 | keep — Δcer 0.235619 |
| 004 | explore (sched refine → `discovery_phase`) | `<|startofprev|>` + 직전 transcript 토큰으로 cross-window context priming (family_003) | 0.187355 | lineage_advance — set seed |
| 005 | refine (ovr `set:refine`, parent iter_003) | greedy → beam_size=2, patience=2.0, length_penalty=1.0 | 0.176289 | micro_bank — del_ratio 0.3631→0.3246 axis 개선 |
| 006 | refine (ovr `set:refine`, parent iter_005) | beam_size 2→4 로 확대 | — (미채점: scope 위반 — `docs/superpowers/plans/...md` 수정 검출) | reject (`scope_violation`) |
| 007 | refine 예정 (ovr `set:refine`, parent iter_005, cooldown `sig_db1166ba` 활성) | — (미채점: scheduler_decision 만 기록되고 candidate 산출물 없음 — run 중단) | — | — |

## CER 궤적

```
stub baseline 0.4126
iter_002  0.411350   (full-coverage 순차 30s chunking — stub 대비 사실상 동률)
iter_003  0.175731   ← run 최저 (timestamp-steered windowing)
iter_004  0.187355   (set 으로 격리)
iter_005  0.176289   (beam=2, champion 0.175731 미돌파 → micro_bank)
```

최종 state: `best_cer 0.175731 @ iter_003`. faster-whisper 앵커 0.1714 에는 미달.

## 무엇이 점수를 만들었나

- **단일 점프는 iter_003**: 30s 단일 윈도우 절단(deletion 80%, length_ratio 0.65)을 timestamp 토큰 기반 seek 조향 windowing 으로 풀어 0.4114 → 0.1757. 이 run 의 개선분 전부가 coverage 회복 한 방.
- iter_002 의 교훈: full-coverage chunking 그 자체(0.4114)는 stub(0.4126)과 거의 같다 — 꼬리 절단을 고쳐도 blind cut 의 윈도우 내 repetition-collapse/조기 EOS 가 deletion 을 그대로 유지함.
- iter_005 의 beam=2 는 corpus_cer 을 못 움직였지만(0.1763 > champion 0.1757) **del_ratio axis 개선으로 micro_bank 에 적립** — Gen7 의 "axis 단위 부분 성과 보존" 슬롯이 의도대로 작동한 사례.

## 한계와 보완점

하네스 검증 관점:

- **확인된 것**: OOM crash → `set:repair` override 전환, discovery_phase 의 explore 강제, set seed(`lineage_advance`), micro_bank axis 판정, signature cooldown 발동(iter_007 의 `sig_db1166ba`), scope 가드의 워크스페이스 외 파일 수정 검출(iter_006) — Gen7 의 판정 기계가 전부 한 번씩 실제로 발화했다.
- **남은 것 / 한계**:
  - iter_006 scope_violation 은 후보가 plan 문서를 만진 것 — 가드는 잡았지만 iter 1회를 통째로 소모. 후보 prompt 측 사전 차단이 없다.
  - iter_007 은 scheduler 결정만 남기고 중단되어 set(refine 예산)이 미완 — decisions.jsonl 도 iter 6 까지만 존재. 짧은 smoke 라 set 의 육성→졸업 전체 사이클은 검증 못 함.
  - 7 iter 중 채점 4회뿐(나머지는 OOM/scope/중단). 점수 면에서는 앵커(0.1714)도 못 넘었으나, smoke 목적상 치명적이지 않음.

## 아티팩트

- iter 디렉토리: `runs/phase3_015_iter_001` ~ `runs/phase3_015_iter_007` (각 `candidate_meta.json`, `score_report.json`, `candidate.diff`, `scheduler_decision.json`)
- 판정/상태: `runs/_archive/phase3_015_decisions.jsonl`, `runs/_archive/phase3_015_state.json`, `runs/_archive/phase3_015_portfolio.json`, `runs/_archive/phase3_015_candidate_meta.jsonl`
- 하네스 맥락: `docs/HARNESS-EVOLUTION.md` Gen7 절
