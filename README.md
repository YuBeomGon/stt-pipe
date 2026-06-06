# AIG STT

한국어 보험 콜센터 통화에 대해 `ctranslate2 + whisper-large-v3-turbo` 추론 파이프라인을
자동 진화시켜 `corpus-level CER` 을 사람이 정한 목표 (`baseline/target_cer.json:target_cer`,
현재 0.10) 이하로 낮추는 실험. `faster-whisper baseline_cer` (현재 0.43) 은 동일 데이터에서의
거리감 측정용 참조 앵커일 뿐 성공 기준은 아니다.

루프 엔진: repository 내부 `harness/` controller. 기존 `autoresearch` 조사는
historical 문서로 보존한다.

정본:
- 문서 지도·SSOT — [`docs/SSOT.md`](docs/SSOT.md)
- 도메인 정의·정규화·가드 — [`docs/STT-PIPELINE-SPEC.md`](docs/STT-PIPELINE-SPEC.md)
- 시스템 설계·디렉토리·3 단계 구조 — [`docs/archive/DESIGN.md`](docs/archive/DESIGN.md)

---

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 첫 실행 시 .cache/ct2_models/ 에 CT2 변환 캐시 자동 생성
```

사전 조건: Linux + CUDA float16 GPU (~14GB+), Python 3.10+, 11 페어:
- `data/raw/wav/AIG_녹취반출_20250715/*_l.wav`
- `data/raw/label/AIG_녹취반출_20250715/*_l.txt`

`ASR_RAW_DATA_ROOT` 로 데이터 루트 오버라이드 가능.

---

## 진행 절차

| 단계 | 무엇 | 어디 |
|------|------|------|
| Phase 1 | Harness 구축 (사람) | [`docs/archive/PHASE1-PLAN.md`](docs/archive/PHASE1-PLAN.md) |
| Phase 2 | 평가 인프라 구축 (사람) | [`docs/archive/PHASE2-PLAN.md`](docs/archive/PHASE2-PLAN.md) |
| Phase 3 | 자체 harness 실행 + 분석 | [`docs/archive/PHASE3-PLAN.md`](docs/archive/PHASE3-PLAN.md) |

---

## Phase 3 진입 → 본 잡 → 종료

각 단계의 *의미*·*실패 모드*·*해석* 은 [`docs/archive/PHASE3-PLAN.md §3`](docs/archive/PHASE3-PLAN.md)
참조. 본 섹션은 copy-paste 가능한 실행 시퀀스만.

### 1. 진입 가드

```bash
# 1-1. holdout 봉인
bash scripts/seal_holdout.sh

# 1-2. 정상 verify 1회
bash scripts/verify.sh
# stub 상태에서는 length_ratio.p05 catastrophic 가드 발동이 정상 (PLAN §3.5).

# 1-3. 의도적 위반 smoke
sed -i '1i import ctranslate2' workspace/transcribe.py
bash scripts/verify.sh
# "verify FAIL [static backend]" + exit 1 확인 후 원복:
git checkout -- workspace/transcribe.py

# 1-4. candidate 컨텍스트 감사 (PLAN §3.7)
python3 scripts/audit_candidate_context.py \
  --candidate-cmd "claude -p --disable-slash-commands --strict-mcp-config --disallowedTools=Bash,WebFetch,WebSearch,Task"
# production runner 가 자동 부착하는 3 flag 와 동일 조합으로 확인.
# `--disallowedTools` 는 `=` 형식 필수 (variadic flag 가 prompt 를 tool 이름으로 먹는 버그 회피).
# 잔여 허용 2 (email/git commits) 만 보고되면 정상. 그 외 누수 → 정리 후 재실행.
# 운영자 interactive `claude` 세션은 영향 받지 않음 — subprocess hardening 만.

# 1-5. 자체 harness dry-run 1 iter
python3 scripts/evolve.py --job-id dry --iters 1 --manual
# 정리 (dry artifacts 가 다음 잡의 ensure_worktree_ready 를 막음 — PLAN §3.8):
git checkout -- runs/_summary/HISTORY.md
rm -f runs/_summary/dry_state.json
rm -rf runs/dry_*
git status   # working tree clean 확인
```

### 2. 본 잡 (예: phase3_002 25 iter)

```bash
python3 scripts/evolve.py \
  --job-id phase3_002 \
  --iters 25 \
  --candidate-cmd "claude -p" \
  --commit-results
```

`--iters > 1` 이면 `--commit-results` 가 **필수** (직전 best 를 git 기준점으로 고정해야
reject 시 안전한 rollback 가능 — PLAN §4).

**시간 / 토큰 가이드** (25 iter 기준, phase3_001 25 iter / 2 h 실측):

| 항목 | 추정 |
|---|---|
| 잡 시간 | ~2 h (iter 당 ~5 min × 25) |
| iter 당 input | ~10 – 12 k token (profile + state + recent + HISTORY tail + diagnosis + auto-push) |
| iter 당 output | ~2 – 4 k token (diff + YAML meta) |
| 총 토큰 | ~300 – 400 k (대부분 input) |
| 한도 주의 | Claude Max 사용 시 토큰 한도 여유 확인. 잡 중 한도 hit = format reject 누적 → abort 가드 발동 위험 |

**잡 시작 전 — HISTORY 리셋** (PLAN §7 정책, 잡 단위 ablation 보호):

```bash
# 직전 잡의 narrative 가 남아있으면 archive 로 이동, 빈 HISTORY 로 시작
if [ -s runs/_summary/HISTORY.md ] && grep -q "^iter" runs/_summary/HISTORY.md; then
  PREV_JOB=$(ls runs/_summary/*_state.json 2>/dev/null | head -1 | xargs -I{} basename {} _state.json)
  mv runs/_summary/HISTORY.md docs/history-archive/HISTORY.${PREV_JOB:-prev}.md
  printf '# Phase 3 HISTORY\n\nIteration narratives. harness 만 append.\n' > runs/_summary/HISTORY.md
fi
```

운영자 interactive `claude` 세션은 `claude -p` subprocess 와 분리됨
(runner 가 candidate cmd 에만 hardening flag 자동 부착, CANDIDATE-CONTEXT §7.6).
**`EVOLVE_NO_HARDEN_CLAUDE=1` 환경변수가 켜진 상태에서 `--iters > 1` 또는
`--commit-results` 가 들어가면 runner 가 잡 시작 거부** — 디버깅 후 unset 잊은
경우의 silent regression 차단.

### 3. 종료 후 분석

```bash
touch runs/_summary/JOB_DONE.lock
python3 scripts/analyze_run.py
python3 scripts/evaluate_holdout.py --unseal
# 산출물 (job_id·날짜·종류가 파일명에 포함 — 여러 잡 누적 시 충돌 방지):
#   docs/reports/<job_id>_REPORT_<YYYY-MM-DD>.md
#   docs/reports/<job_id>_HOLDOUT_<YYYY-MM-DD>.md
#   docs/reports/<job_id>_HOLDOUT_<YYYY-MM-DD>.json
# holdout 평가 후 자동으로 chmod 000 재봉인.
```

---

## set 모드 · worktree · 병렬 · gated promotion (phase1.5 / 2 / 3)

기본 single-shot 루프 위에 얹힌 진화 모드. 설계 상세는
[`docs/HARNESS-MECHANICS.md`](docs/HARNESS-MECHANICS.md) §3(commit 정책)·§12(set),
동작/튜닝 백로그는
[`docs/proposals/2026-06-05-phase2-backlog-findings.md`](docs/proposals/2026-06-05-phase2-backlog-findings.md).

요약:
- **phase1.5 — metadata off-git.** `runs/` 전부 gitignore. git commit 은 **코드
  checkpoint 전용**(`keep`/`success`/`lineage_advance`/`reset`), metadata
  (state/portfolio/decisions/candidate_meta/HISTORY)는 atomic/append 로 디스크에 durable.
  scope 가드는 tracked 표면=평범한 `git status`, ignored `runs/` 표면=후보 전/후
  **filesystem 스냅샷 diff**(`git status --ignored` 아님 — 기존 ignored 파일 오탐 방지).
  rollback 은 정확한 fs delete(`git clean` 금지).
- **phase1 set — `--set-budget N`(>1).** champion 보다 나쁜 explore 를 버리지 않고
  bounded set(explore→repair/refine, budget = explore 1 + repair ≤2 + refine ≤3)으로
  육성. `champion` 은 별도 git ref(승격 코드), HEAD 는 lineage head.
- **phase3 — gated promotion.** 승격은 직렬 gate 경유: `.git/champion_promote.lock`
  (`fcntl.flock`) → live champion CER 재검증 → `git update-ref` **CAS** 로 champion 에
  `transcribe.py` 만 splice. 기록은 `runs/_summary/promotion_map.jsonl`.
- **phase2 — worktree + 병렬.** 잡마다 champion 에서 worktree cut → 격리 실행, 공유
  champion 승격은 위 gate 로 직렬화 → 동시 잡 안전.

### 사전 준비 (fresh 시작)

```bash
# champion ref 를 시작 코드(예: stub commit)로, promotion_map 초기화,
# workspace 를 동일 코드로 맞추고 commit (HEAD==시작코드 라야 ensure_worktree_ready 통과)
git branch -f champion <start-commit>
rm -f runs/_summary/promotion_map.jsonl
git restore --source=champion -- workspace/transcribe.py
git add workspace/transcribe.py && git commit -m "chore: reset workspace to start code"
# job-id 재사용 시 직전 state 가 있으면 resume 됨 → fresh 면 새 id 쓰거나 state 이동:
#   mv runs/_summary/<job>_*  runs/_archive/   (또는 새 --job-id)
```

> `runs/` 가 gitignore 라, 위 §1 진입가드의 `git checkout -- runs/_summary/HISTORY.md`
> 같은 git 기반 정리는 더 이상 통하지 않는다(파일이 untracked). 정리는 `rm` 으로.

### A. 단일 lane (기존 방식 + gate 자동 적용)

```bash
python3 scripts/evolve.py \
  --job-id phase3_017 \
  --iters 10 \
  --candidate-cmd "claude -p" \
  --commit-results \
  --set-budget 4
```

`--set-budget >1` 이면 set 육성이 켜지고 (`--commit-results` 필수), 승격은 자동으로
gate(flock + 재검증 + CAS-splice)를 거친다. worktree·병렬 미사용 — 메인 repo 에서 직접.

### B. worktree + 병렬 (launcher)

```bash
python3 scripts/launch_parallel.py \
  --job-ids phase3_017,phase3_018 \
  --iters 10 \
  --candidate-cmd "claude -p" \
  --set-budget 4 \
  --concurrency 2
```

- 잡마다 `../wt-<job_id>` worktree 를 champion 에서 cut. (`--worktree-root` 로 위치 변경.)
- `--concurrency N`: 동시 실행 잡 수. GPU runtime cap = `baseline × 7`(`RUNTIME_HARD_MULTIPLIER`)
  이라 동시 verify 경합에는 여유가 있으나, **진짜 제약은 `claude -p` N 배 rate**(세션/토큰
  한도). 처음엔 `--concurrency 1`(worktree+gate 만 검증) 또는 `2` 권장.
- 끝나면 worktree 정리: `git worktree remove ../wt-<job_id>` (브랜치 `job/<job_id>` 는 보존).

---

## simple-evolve (신규 단순 루프)

기존 old-harness (set 모드·worktree·gated promotion) 와는 **별개**의, 의도적으로
단순화한 자체-진화 루프. flat never-pruned archive + LLM-driven move (parent 선택 →
candidate 가 `workspace/transcribe.py` 수정) + verify keep-if-better. scheduler /
lineage / portfolio / cooldown / signature / promotion / gitops 없음. 엔트리포인트는
`scripts/evolve_simple.py`. 노브: `--job-id --iters --candidate-cmd --directive
--explore --ban --pin --parent-policy`. **`--holdout-every` 는 제거됨** (holdout 은
이제 운영자 수동).

```bash
python scripts/evolve_simple.py --job-id simple_001 --iters 20 \
  --explore 0.5 --parent-policy llm
```

리더보드:

```bash
python scripts/archive_summary.py --job simple_001
```

### Holdout (운영자 수동)

루프는 holdout 을 **전혀 건드리지 않는다** — in-loop CER (0715) 만 계산하고
best 를 `runs/_summary/<job>_state.json::best_hyp_id` 에 기록한다. 잡이 끝난 뒤,
운영자가 봉인된 holdout 으로 best 를 **수동 검증**한다:

```bash
python scripts/evaluate_holdout.py --unseal --job-id <id>
# state 의 best_hyp_id 를 anchor 로 0715 eval run 을 찾아 holdout 평가.
```

**주의**: 자동 재봉인 (`chmod -R 000`) 은 재귀 도중 "Permission denied" 로 실패할
수 있다 (candidate user 로 실행 시 라이브 검증에서 실제 실패함 — 이것이 자동
holdout 호출을 제거한 이유). **실행 후 반드시 봉인이 복구됐는지 확인**한다:

```bash
ls -ld data/raw/wav/AIG_녹취반출_20250813 data/raw/label/AIG_녹취반출_20250813
# 두 디렉토리 모두 d--------- (perm 000) 이어야 정상.
```

`d---------` 가 아니면 재봉인:

```bash
bash scripts/seal_holdout.sh   # 또는 두 디렉토리에 chmod 000
```

holdout 접근은 운영자 파일 권한이 필요하다 — candidate 는 sandbox 되어 있으며
holdout 을 절대 읽어선 안 된다.

---

## 절대 금지

요약만 — 전체 목록은 [`docs/STT-PIPELINE-SPEC.md §11`](docs/STT-PIPELINE-SPEC.md).

- holdout (`AIG_녹취반출_20250813`) 접근
- 모델·backend 변경 (whisper-large-v3-turbo + ctranslate2 고정)
- `baseline/*.json` 재측정 / 덮어쓰기

---

## 문서 색인

| 문서 | 용도 |
|------|------|
| [`docs/SSOT.md`](docs/SSOT.md) | 문서별 정본 책임 지도 |
| [`docs/HARNESS-MECHANICS.md`](docs/HARNESS-MECHANICS.md) | 루프 동작지도 (§3 commit 정책, §12 set 모드) |
| [`docs/proposals/2026-06-05-phase2-backlog-findings.md`](docs/proposals/2026-06-05-phase2-backlog-findings.md) | 라이브 검증 발견사항·튜닝 백로그 (F1/F2) |
| [`docs/STT-PIPELINE-SPEC.md`](docs/STT-PIPELINE-SPEC.md) | 문제 정의·정규화·가드 (정본) |
| [`docs/archive/DESIGN.md`](docs/archive/DESIGN.md) | 시스템 설계 (3 단계 구조, 디렉토리) |
| [`docs/archive/PHASE1-PLAN.md`](docs/archive/PHASE1-PLAN.md) | Harness 구축 단계별 |
| [`docs/archive/PHASE2-PLAN.md`](docs/archive/PHASE2-PLAN.md) | 평가 인프라 구축 단계별 |
| [`docs/archive/PHASE3-PLAN.md`](docs/archive/PHASE3-PLAN.md) | 자체 harness 실행 + 분석 절차 |
| [`docs/archive/PHASE3-STATUS.md`](docs/archive/PHASE3-STATUS.md) | Phase 3 DoD 체크 상태 |
| [`docs/archive/PHASE3-LOOP.md`](docs/archive/PHASE3-LOOP.md) | Phase 3 루프/노출/판정 구조 Mermaid |
| [`docs/archive/AUTORESEARCH.md`](docs/archive/AUTORESEARCH.md) | 이전 autoresearch 조사 기록 (historical) |
| [`docs/archive/SELF-EVOLVE-HARNESS-SPEC.md`](docs/archive/SELF-EVOLVE-HARNESS-SPEC.md) | 참고용 일반 하네스 원리 (정본 아님) |
| [`AGENTS.md`](AGENTS.md) | 에이전트 공통 규약 |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code 보충 |
