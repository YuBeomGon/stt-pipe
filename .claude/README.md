# .claude (Phase 3 active body)

본 디렉토리는 *swap 전* 상태에서는 `.claude.alt/`, *swap 후* 활성화되면
`.claude/` 가 된다 (사람이 `bash scripts/swap_claude.sh` 1 회).

활성화 시 Claude Code 가 다음을 적용:

1. **`settings.json` 의 `permissions.deny`** — `judge/`, `frozen/`, `baseline/`,
   `assets/`, 보호 scripts, holdout 의 Edit/Write/Read 차단. swap/seal 스크립트
   Bash 호출 차단. holdout chmod 직접 호출 차단.
2. **`hooks/restrict_workspace.py`** (PreToolUse Edit/Write/MultiEdit) —
   `workspace/transcribe.py` + `runs/` + `autoresearch/` 외 편집 거부.
3. **`hooks/block_swap_and_seal.py`** (PreToolUse Bash) — swap_verify /
   swap_claude / seal_holdout / evaluate_holdout --unseal / holdout chmod /
   `.claude` mv·rm / verify.sh mv 패턴 거부.

이 둘은 ENV 우회 불가 — autoresearch 의 `AR_DISABLE_*` 와 무관.

활성 본문이 `.claude/` 인지 빠른 확인:

```bash
ls .claude/                # settings.json + hooks/ 가 있으면 Phase 3 본문 활성
ls .claude/hooks/          # restrict_workspace.py + block_swap_and_seal.py
```

비활성 (Phase 1·2) 본문은 `.gitkeep` 만 있고 hooks/settings 부재.
