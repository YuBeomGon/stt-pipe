# HARNESS-EVOLUTION — harness 방법론 변천사

작성일: 2026-06-10. git 브랜치/커밋 히스토리 + HISTORY 아카이브 + 회고 문서 기준으로 재구성.

이 문서는 **"harness 가 어떻게 지금 모습이 됐는가"** 의 정리다. 각 시점의 *동작 상세* 정본은:

| 정본 | 커버 |
|---|---|
| [`HARNESS-MECHANICS.md`](HARNESS-MECHANICS.md) | old-harness (6모드 portfolio 엔진, ~phase3_014 시점) 코드 동작 레퍼런스 |
| [`HARNESS-ALGORITHM.md`](HARNESS-ALGORITHM.md) | refactor 후 old-harness 전체 알고리즘 (set·promotion·worktree 포함) |
| [`HARNESS-REDESIGN.md`](HARNESS-REDESIGN.md) | worktree-first 리팩토링 설계 보고서 |
| [`superpowers/plans/2026-06-06-simple-evolve-implementation.md`](superpowers/plans/2026-06-06-simple-evolve-implementation.md) | simple-evolve 구현 플랜 |

---

## 0. 한눈에 — 세대 계보

브랜치는 사실상 **선형 계보**다 (각 브랜치가 직전 브랜치 위에 누적):

```
main (Phase1/2 인프라, 5/28)
 └─ phase3 ──────────────── Gen1: autoresearch 루프 + swap 가드 (phase3_001)
     └─ phase3-local ────── Gen2: 자체 harness 탄생 — A' profile + format gate + hardening (phase3_002)
         └─ phase3-run50 ── Gen3: 발견형 루프 — findings ledger + cold-restart (phase3_003·004)
             ├─ phase3-run50-rerun (r50d param-sweep 재실행 전용 분기)
             └─ phase3-diagnosis-feedback ── Gen4: 진단 주입 + synthesize 모드 (phase3_005)
                 └─ phase3-portfolio-evolution ── Gen5: portfolio + 5+1모드 scheduler (phase3_006·007)
                     └─ phase3-family-lineage ── Gen6: lineage family + plateau-escape + DIVERGE (phase3_008~014)
                         └─ refactor-harness ── Gen7: runs off-git + worktree + gated promotion + set (phase3_015~030)
                             └─ simple-evolve ── Gen8: 의도적 단순화 (simple_001~006)
champion ref: Gen7 부터 승격 코드 전용 git ref
```

---

## 1. 세대별 정리

### Gen0 — 평가 인프라 (main, 5/28, Phase 1·2)

- **무엇**: judge(채점기) + frozen backend(CT2 봉인) + workspace stub + baseline/σ proxy 측정 + holdout 분리. analyze_run / evaluate_holdout / REPORT 템플릿.
- **가드 구조의 원형**: verify 가드레일을 *swap 쌍* 으로 설계 — `verify.sh.alt` + `verify_check.py` + `swap_verify.sh`, `.claude/` guard 자산(settings.json + PreToolUse hooks) + `swap_claude.sh`. **가드레일을 켜고 끄는 스위치**가 처음부터 운영 개념이었다 (Phase 1·2 는 사람 주도라 OFF, Phase 3 진입 시 ON).
- **교훈**: target_cer(사람 목표 0.10)와 baseline_cer(faster-whisper 앵커)의 분리를 이 시점에 결정.

### Gen1 — autoresearch 루프 (phase3, phase3_001)

- **무엇**: 외부 `autoresearch` 플러그인이 루프 엔진. iter 마다 git commit, 실패 시 `Revert` 커밋 (히스토리에 experiment/Revert 쌍이 그대로 남음). `append_history.sh` 로 HISTORY 수기 규약.
- **왜 버림**: 루프 제어·기록·rollback 이 전부 범용 도구에 의존 → 도메인 가드(형식 검증, 다양성, 진단)를 끼워 넣을 자리가 없음. → 자체 harness 로 전환, autoresearch 는 historical 문서로 보존.

### Gen2 — 자체 harness 탄생 (phase3-local, phase3_002)

- **핵심 도입**:
  - **A' candidate profile + runner format gate** — 후보가 YAML meta(가설/배움)를 의무 제출, 형식 위반은 reject. "후보의 자기보고를 구조화" 의 시작.
  - **candidate subprocess hardening** — `claude -p` 에 `--disable-slash-commands --strict-mcp-config --disallowedTools` 자동 부착. 운영자 interactive 세션과 후보 세션의 분리.
  - **candidate 컨텍스트 누수 감사** — `audit_candidate_context.py` 로 누수 5→4→2 정리. CLAUDE.md candidate session gate (`=== BEGIN CANDIDATE PROFILE ===`).
  - analyze D축(다양성 메트릭), 리포트 `docs/reports/` 이전·명명 규칙.
- **교훈**: prompt delimiter `---`→`===` (claude CLI argv 파서 버그 우회) 같은 LLM-CLI 연동 함정들이 이 세대에서 정리됨.

### Gen3 — 발견형 루프 (phase3-run50, phase3_003·004)

- **핵심 도입**: **표면 탐사 + findings ledger + cold-restart** — 후보가 backend 표면(decode kwargs 등)을 탐사해 발견을 ledger 에 누적, 정체 시 cold-restart wildcard.
- **튜닝 이력**: ledger 를 잡 전체 jsonl 에서 빌드(F1, compound), banking 게이트 0.01→0.002(F2, 작은 실질 개선 lock-in), cold-restart threshold 5→3(F3).
- **부산물**: phase3_003 은 무효 실행으로 폐기. run50-rerun 분기에서 param-sweep 도구 + **세션 한도 backoff** + **evaluator-crash reason 을 다음 후보에게 feedback** 이 추가됨 (이후 세대의 표준이 됨).

### Gen4 — 진단 주입 (phase3-diagnosis-feedback, phase3_005)

- **핵심 도입**:
  - **진단 주입** — 후보 프롬프트에 error profile + failure signature (diagnosis_report 기반) 제공. "추측 대신 측정된 실패축을 보여준다".
  - **synthesize 모드** — 유망했던 reject 들을 조합하는 스케줄 슬롯.
  - explore/exploit 리네임, `EXPLORE_RATIO_FLOOR` 0.2→0.5.
- **운영 가드 강화**: runtime hard-cap multiplier 3→5→7, hung candidate wall-clock kill, 운영 노브 `harness/config.py` 로 SSOT 집중.

### Gen5 — Portfolio Evolution (phase3-portfolio-evolution, phase3_006·007)

- **핵심 도입**: [proposal: Portfolio Evolution Harness]
  - **harness-derived family/signature** — 후보 자기보고가 아니라 harness 가 diff 에서 family 를 도출.
  - **portfolio + micro_bank + decision trace** — 부모 후보군을 6종 bank 로 유지, 모든 결정 jsonl 기록.
  - **5+1 모드 scheduler** (explore/refine/repair/synthesize/…) + evaluated 기준 budget 카운팅 + soft cooldown + attempt cap.
- **사건**: phase3_007 crash-loop 폐기 → "crash-before-first-best 는 explore 가 아니라 repair" 스케줄러 수정. **baseline re-anchor 0.4685→0.1714** (prod-left faster-whisper 디코딩/VAD 기준으로 앵커 현실화).

### Gen6 — Lineage family + 정체 탈출 (phase3-family-lineage, phase3_008~014)

- **핵심 도입**:
  - **lineage-aware family** — 파생 모드는 부모 family 상속 (가짜 신규 family 방지).
  - **near-best 풀 보존(best×1.20) + refine parent 회전** — F2(다양성 생존) 대응.
  - **plateau → 주기적 burst + 조합(부모 주입) 모드**, discovery floor 재조정 (phase3_011 조기수렴 대응).
  - **Phase 1 plateau-escape** — monotone best / floor cap / dead-end memory (phase3_013, best 0.1539 달성 run).
  - **DIVERGE directive** (phase3_014) — 명시적 탈앵커 탐색 지시.
  - 세션/토큰 한도 **backoff 사다리** (5·10·20·40·80분).
- **교훈 (phase3_014 분석)**: explore 단독으로는 champion 을 못 이김 — 개선은 refine/repair 에서 나옴. diversity_stall 과발동. "다양성을 *지시* 하는 것"의 한계 확인.

### Gen7 — 구조 리팩토링 (refactor-harness, phase3_015~030)

근본 원인 진단에서 출발: **git HEAD 하나가 4 역할(champion/lineage/작업/기록)을 겸함** → explore 가 reject 되면 champion 까지 rollback 되어 계보를 못 키움 (C1/C2). 해법 3단:

  - **phase1.5 — metadata off-git**: `runs/` 전부 gitignore. commit 은 코드 checkpoint 전용(keep/success/lineage_advance/reset). scope 가드는 tracked=git status + ignored=**filesystem snapshot diff** (git clean 금지, 정밀 fs rollback).
  - **phase1 — lineage set** (`--set-budget`): champion 보다 나쁜 explore 를 버리지 않고 bounded set(explore→repair≤2/refine≤3)으로 육성. **champion 을 별도 git ref 로 분리** — HEAD 는 lineage head.
  - **phase2·3 — worktree 병렬 + gated promotion**: 잡마다 champion 에서 worktree cut, 승격은 직렬 gate (`fcntl.flock` → live 재검증 → `git update-ref` CAS) + promotion_map 기록. F1~F4 promotion 버그 수정 (lost-race 복구, stub-seed 불일치 등).
- **검증 run**: phase3_015~019 (gate/worktree smoke), **phase3_030 (107 iter 본 run, 0.41→0.163)**.
- **교훈 (phase3_030 분석)**: from-scratch 재현은 검증됐으나 **다양성은 "생성"되고 "육성"되지 않음** — 한 계보가 74% 과점, 신규 family 조기 사망, 앙상블 무력 → 0.154 미달. 구조(스케줄러·portfolio·set)를 늘리는 방향의 수확체감 확인.

### Gen8 — 의도적 단순화 (simple-evolve, simple_001~006)

- **무엇**: scheduler / lineage / portfolio / cooldown / signature / promotion / gitops **전부 제거**. 남긴 것:
  - **flat never-pruned archive** (`harness/archive.py`) — 모든 평가 후보의 코드 전문 + diff + 점수를 그대로 보존 (복원성 최상).
  - **LLM-driven move** — parent 선택 자체를 LLM 에 위임 (`--parent-policy llm`, random/best 옵션), explore 비율·directive·ban/pin 은 노브로.
  - verify keep-if-better 한 줄 정책. verify/reject 에러를 다음 후보 프롬프트에 feedback.
  - rate-limit backoff 사다리 (5·10·20·40·80·80·80분, 소진 시 `aborted_rate_limit` + 동일 job-id resume).
- **holdout 수동화 결정**: 자동 holdout 평가 제거 — candidate user 권한에서 `chmod 000` 재봉인이 재귀 도중 실패하는 실제 사고 → 루프는 holdout 을 전혀 안 건드리고, 운영자가 잡 종료 후 `evaluate_holdout.py --unseal` 수동 실행 + 봉인 복구 확인.
- **현황**: simple_003 이 8 iter 만에 0.1757 (holdout 검증 별도 리포트). simple_006 은 후보 출력 형식 오류로 100 iter 전멸 (harness 문제가 아니라 후보 세션 환경 문제).

---

## 2. 설계 결정 로그 (전환점)

| # | 결정 | 계기 | 결과/교훈 |
|---|---|---|---|
| 1 | autoresearch → 자체 harness | 범용 루프에 도메인 가드를 못 끼움 | 형식 gate·hardening·진단 주입이 가능해짐 |
| 2 | 후보 YAML meta 의무화 (format gate) | 자기보고 비구조화 | reject 사유의 정량화, ledger/카탈로그의 원천 |
| 3 | candidate subprocess hardening + context 감사 | 후보가 운영 컨텍스트(스킬·도구) 누수 접근 | 운영자/후보 세션 완전 분리, `EVOLVE_NO_HARDEN_CLAUDE` 안전장치 |
| 4 | findings ledger + cold-restart | 같은 표면만 반복 탐사 | 탐사 누적은 유효, cold-restart 는 threshold 튜닝 필요 |
| 5 | 진단 주입 (error profile/failure signature) | 후보가 실패 원인을 추측 | "측정된 실패축 제공" 이후 세대 표준 |
| 6 | harness-derived family (자기보고 불신) | 후보가 신규성 과대보고 | signature/cooldown 의 기반 |
| 7 | baseline re-anchor (0.4685→0.1714) | 앵커가 비현실적으로 느슨 | 거리감 측정 정상화 |
| 8 | crash 분류: explore 아닌 repair | phase3_007 crash-loop | 스케줄러가 실패 종류를 구분해야 함 |
| 9 | plateau-escape (burst·dead-end memory·DIVERGE) | 조기수렴 (011·013·014) | 부분 효과. "다양성 지시" 한계 — explore 단독으론 못 이김 |
| 10 | **champion ref 분리 + runs off-git** | HEAD 1개가 4역할 (C1/C2) — reject 가 champion 을 되감음 | rollback 정밀화, 병렬 가능 구조 |
| 11 | lineage set (지는 explore 육성) | 유망 explore 가 즉사 | 구조는 작동, but phase3_030: 육성이 한 계보 과점을 못 막음 |
| 12 | gated promotion (flock+재검증+CAS) | worktree 병렬 시 champion 경합 | 동시 잡 안전. lost-race 복구까지 F1~F4 로 보강 |
| 13 | auto-holdout 제거 → 운영자 수동 | candidate 권한에서 재봉인 실패 (실사고) | 봉인 무결성 > 자동화 편의 |
| 14 | **전부 버리고 simple-evolve** | 구조 추가의 수확체감 (030 분석) | 복잡도를 LLM 판단(parent 선택)으로 치환. 검증 진행 중 |

---

## 3. 가드레일 카탈로그 (층위별)

| 층위 | 장치 | 도입 세대 |
|---|---|---|
| **데이터 봉인** | holdout(0813) `chmod 000` + `seal_holdout.sh` + 평가 후 자동 재봉인 확인 절차 | Gen0, 수동화는 Gen8 |
| | `_FORBIDDEN_BATCHES` hard-code deny (measure_baseline / build_audio_profile) | Gen0 |
| **backend 봉인** | `frozen/` 편집 금지 + verify static guard (`import ctranslate2` 등 직접참조 시 FAIL) | Gen0 |
| **후보 격리** | `claude -p` hardening flags 자동 부착, `--disallowedTools=Bash,WebFetch,WebSearch,Task` | Gen2 |
| | `audit_candidate_context.py` 컨텍스트 누수 감사 | Gen2 |
| | CLAUDE.md candidate session gate | Gen2 |
| | `.claude/` PreToolUse hooks (workspace 제한, swap/seal 차단) | Gen0~ (legacy 자산) |
| **가드 스위치** | `swap_claude.sh` / `swap_verify.sh` — Phase 1·2(사람)에서 가드 OFF, Phase 3 ON | Gen0 (자체 harness 후 정리 대상) |
| **품질 가드** | `harness/guards.py` — hallucination/length/repetition/coverage 를 guard_baseline 대비 budget 판정, 산술 불일치·실행 실패 hard-fail | Gen2~ |
| **자원 가드** | runtime cap `baseline×7`, hung candidate wall-clock kill | Gen4 |
| | 세션/토큰 한도 backoff 사다리 + `EVOLVE_NO_HARDEN_CLAUDE` 잡 시작 거부 | Gen6/Gen2 |
| **scope 가드** | tracked=git status, ignored `runs/`=fs snapshot diff, 정밀 fs rollback (git clean 금지) | Gen7 |
| **형식 가드** | YAML meta format gate + format-reject 누적 abort | Gen2 |
| **승격 가드** | flock 직렬화 + live CER 재검증 + git ref CAS + promotion_map | Gen7 |

## 4. 평가축 변천

1. **corpus_cer 단일** (Gen0~) — 항상 primary. 의미 있는 개선 = Δcer ≥ 2σ (noise_floor).
2. **error_breakdown** sub/del/ins 비율 (Gen0 judge) — 후보 프롬프트의 "dominant axis" 서사의 원천.
3. **diagnosis_report** — per-file focus + failure signature (hallucination/repetition/length/coverage) (Gen4 부터 프롬프트 주입).
4. **D축 다양성 메트릭** (Gen2 analyze) → family/signature 분포 (Gen5~) → 030 회고에서 "계보 과점 74%" 진단의 근거.
5. **holdout** — 잡 단위 1회, 오버피팅 감시 (0715 eval vs 0813 holdout). simple_003 에서 in-loop 0.1757 → holdout 별도 리포트로 검증.
6. **runtime budget** — `total_inference_time_s ≤ baseline×배수`.

## 5. git 정책 변천

| 세대 | 정책 |
|---|---|
| Gen1 | iter 마다 commit, 실패는 `Revert` 커밋 (히스토리 오염) |
| Gen2~6 | keep 시 commit = rollback 기준점 (`--iters>1` 이면 `--commit-results` 필수). HEAD 가 champion/lineage/작업 겸임 |
| Gen7 | **commit = 코드 checkpoint 전용** (keep/success/lineage_advance/reset), metadata 는 디스크 atomic/append. champion = 별도 ref, 잡 = worktree 브랜치(`job/<id>`), 승격 = CAS splice |
| Gen8 | **gitops 없음** — 아카이브가 디스크에 코드 전문 보존, git 은 운영자 수동 |

## 6. 종합 교훈

1. **가드는 사고에서 자랐다** — 컨텍스트 누수(Gen2), crash-loop(Gen5), 재봉인 실패(Gen8 수동화) 등 실제 사고가 각 가드의 직접 계기.
2. **후보 자기보고는 믿지 않는다** — format gate(Gen2) → harness-derived family(Gen5) → 진단 주입(Gen4)으로 일관된 방향.
3. **다양성은 지시·구조로 안 만들어졌다** — DIVERGE(Gen6), set 육성(Gen7) 모두 부분 실패. phase3_030: 생성O 육성X. 이것이 simple-evolve(LLM parent 선택 + never-pruned archive)의 직접 동기.
4. **git 역할 분리가 병렬화의 전제** — HEAD 4역할 문제(C1/C2)를 풀어야 worktree·gated promotion 이 가능했다.
5. **단순화도 설계다** — Gen8 은 기능 제거가 아니라 "복잡도를 어디에 둘 것인가(코드 → LLM 판단)"의 재배치.
