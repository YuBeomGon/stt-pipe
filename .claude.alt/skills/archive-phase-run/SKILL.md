---
name: archive-phase-run
description: Use when a phase-N evolution run (phase3_NNN) finishes and the operator wants to organize and push the results — sanitizes per-iter experiment logs into a PII-free tar.gz, writes a retrospective, and commits/pushes. Enforces "0 customer PII" before any commit.
---

# Archive a phase run for push (PII-safe)

`runs/<hyp_id>/` 는 `.gitignore` 라 git 에 안 들어간다(고객 PII 포함). 회고·재현용
보존은 **PII 제거 아카이브로만** 남기고, 그 전에 반드시 스캐너로 검증한다.

## 불변식 (절대)

- `runs/<job>_iter_*/` 원본을 git 에 **직접 커밋 금지** — 콜ID·마스킹폰 누출.
- 아카이브 tar 는 **`scripts/sanitize_runs.py` 가 PII 스캔 통과한 경우에만** 생성된다.
  스캔 실패(콜ID/마스킹폰/batch/홈경로 잔존) 시 tar 를 만들지 않으니, **실패하면 멈추고
  redaction 을 고친다.** 통과 못 한 채 커밋하지 않는다.
- 커밋에서 **제외**: `.claude/`·`.claude.alt/` swap-state(skills 제외), `workspace/transcribe.py`
  실험 잔재. (이전 패턴: prior 아카이브 커밋 참조 — `git log -- docs/history-archive/runs/`)

## 절차

1. **PII 검증 (tar 없이)**
   ```bash
   python scripts/sanitize_runs.py --job-id phase3_NNN --scan-only
   ```
   `❌` 면 멈추고 `scripts/sanitize_runs.py` 의 redaction/매핑 보강 → 통과까지 반복.
   새 파일명 포맷(예: 마스킹폰 `010XXXX0120`)이 나오면 스캐너·매핑에 추가하고
   `tests/test_sanitize_runs.py` 에 케이스 고정.

2. **아카이브 생성**
   ```bash
   python scripts/sanitize_runs.py --job-id phase3_NNN
   # → docs/history-archive/runs/phase3_NNN_runs_sanitized.tar.gz
   ```

3. **독립 교차검증** (스캐너만 믿지 말 것)
   ```bash
   tmp=$(mktemp -d); tar xzf docs/history-archive/runs/phase3_NNN_runs_sanitized.tar.gz -C "$tmp"
   grep -rF "AIG_녹취반출" "$tmp" | wc -l      # 0 이어야
   grep -rF "/home/" "$tmp" | wc -l           # 0 이어야
   grep -rEh "[0-9]X{2,}[0-9]|X{2,}[0-9]" "$tmp" | wc -l   # 마스킹폰 0
   rm -rf "$tmp"
   ```

4. **회고 작성** `docs/retrospectives/YYYY-MM-DD-phaseN-NNN-retrospective.md`
   - 헤더표(잡/ground truth/결과/비교) · banked 궤적(keep, git 기준) · 좋은 점 ·
     아쉬운 점 · 개선 포인트. ground truth 는 git commit + `runs/_summary/<job>_state.json`.
   - 포맷 참조: 직전 회고 (`docs/retrospectives/` 최신).
   - (선택) 자동 REPORT 는 `scripts/analyze_run.py` — 단 wav 파일명이 박히므로 커밋 전
     익명화 필요. 분석이 회고에 다 담기면 생략 가능.

5. **옛 리포트 이동** (있으면) → `docs/history-archive/reports/`.

6. **커밋 + 푸시** (PII 스캔 통과 확인 후에만)
   ```bash
   git add scripts/sanitize_runs.py tests/test_sanitize_runs.py \
           docs/history-archive/runs/phase3_NNN_runs_sanitized.tar.gz \
           docs/retrospectives/<새 회고>.md
   git status --short   # .claude swap-state / workspace 잔재가 staged 아닌지 확인
   git commit -m "docs: phaseN_NNN 회고 + 실험로그 sanitize 아카이브"   # Co-Authored-By 트레일러
   git push
   ```

## 메모

- sanitizer 가 제거하는 것: 20자리 콜ID, 마스킹폰(`010XXXX0120` 류), batch
  (`AIG_녹취반출_*`), 절대 홈경로, hallucinated_spans 텍스트, 날짜·시각 파편(파일명 stem 통째 치환).
- 스캐너는 CER/length_ratio float 소수부를 콜ID/전화로 **오탐하지 않는다** — 새 패턴
  추가 시 이 오탐 회귀를 테스트로 막을 것.
