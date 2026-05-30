# phase3_002 Preflight — Codex Review 판정 + 진입 결정

- 일시: 2026-05-29
- 대상: codex preflight review ([`2026-05-29-1931-phase3-002-preflight-review.md`](2026-05-29-1931-phase3-002-preflight-review.md))
- 결정: **codex C1/C2 deep fix skip, 잔여 보강 후속, phase3_002 진입**
- 작성: interactive 운영자 (jake) + claude opus 4.7

---

## 1. Codex finding 검증 요약

| # | claim | 코드 검증 | valid? |
|---|---|---|---|
| C1 | `transcribe()` 가 `open("data/raw/label/.../{file_id}.txt")` 로 정답 read 가능 | `judge/evaluate.py:108` 환경변수 노출 + subprocess 안 Python 자유 | ✅ |
| C2 | `transcribe()` 의 `atexit` 가 `runs/<hyp_id>/score_report.json` 덮어쓰기 가능 | `harness/runner.py:262` post-verify 가 `runs/<hyp_id>/` 통째 허용 | ✅ |
| C3 | static check 후 `transcribe()` 가 자기 source 덮어쓰면 commit ≠ verified | static check = pre-verify, post-verify 가 workspace 변경 무시 | ✅ |
| C4a | settings.json 에 Grep/Glob/LS deny 없음 | 확인 결과 없음 | ✅ |
| C4b | `--disallowedTools=Read` 부분 지정 시 default merge 안 됨 | 런타임 확인 — Bash 노출됨 | ✅ (내 코드 버그) |
| C4c | wrapper (`env claude -p`) 우회 | docstring 명시 (intentional) | ✅ (footgun) |
| I1 | README post-job analyze_run `--job-id` 누락 | `README.md:113` 확인 | ✅ |
| I2 | `scripts/verify.sh` grep < `harness.verify` AST | `from X import Y` 못 잡음 | ✅ |
| I3/I4 | preflight checkbox / dry artifacts | cosmetic | ✅ |

**모두 valid**. codex 가 기술적으로 정확함.

---

## 2. 그러나 — 우리 context 의 위협 모델

### 2.1 핵심 통찰 — settings.json 한계

```
[채널 1: claude -p subprocess]              [채널 2: judge.evaluate subprocess]
운영자 → runner → claude -p <prompt>          runner → python -m judge.evaluate
                  ↓                                            ↓
                  claude Tool 사용                             import workspace.transcribe
                  - Read / Edit / Bash / Grep                  transcribe(audio, sr) 실행
                       ↑                                              ↑
                       └ settings.json deny 적용 ✅                  └ settings.json 무관 ❌
                                                                       (Python OS-level)
```

직전 hardening (settings.json Read deny, `--disallowedTools Bash`, body
inject) 는 **claude `-p` Tool 채널만** 정리. **candidate 가 *작성한* Python
코드가 verify 시점에 실행될 때는 전혀 적용 X**:
- judge.evaluate subprocess 안에서 OS 권한 자유
- `open()`, `atexit`, file mutation 다 가능
- chmod 도 안 됨 — judge 도 label read 필요 (점수 계산용), 같은 process

→ **현재 유일한 방어 = profile 의 "Don't read forbidden files" 가이드 +
LLM 협조**.

### 2.2 진짜 막으려면

| 방법 | 비용 | 효과 |
|---|---|---|
| (a) AST static deny 패턴 (`open`, `Path.read_text`, `os.environ`, `data/raw`, `label/`, `atexit` 등) | 1 h | 정직한 시도 catch. `getattr(__builtins__, "open")` / base64 우회 가능 |
| (b) transcribe 를 격리 subprocess + chroot / namespace + IPC | 1+ d | 진짜 차단. judge 만 label read, transcribe 는 격리. 큰 rearchitecting |
| (c) seccomp / pyseccomp | 0.5 d | OS-level 차단, Linux 한정, 일부 라이브러리 호환 이슈 |
| (d) holdout (현재) | 0 | post-hoc detect, prevent X |

---

## 3. 결정 근거 — codex 위협 모델 vs 우리 context

### 3.1 phase3_001 empirical evidence

같은 갭 (그때는 hardening *더 약함*) 에서:
- Bash 자유 ✅ — `cat data/raw/label/.../xxx.txt` 가능
- Read 자유 ✅ — judge code 읽기 가능
- atexit / post-static mutation ✅ — 가능
- **결과**: 25 iter 모두 cheating 0. chunking / decoding 정상 시도, best 0.2545 (정상 범위)

LLM 이 profile 의 "Do not read forbidden files" + "Anti-patterns" 따랐음.
*증거가 있는 비공격 행동*.

### 3.2 Cheating 가시성

발생 시:
- CER 0.0 갑자기 등장 → 운영자 즉시 의심
- per_file.jsonl 완벽 매칭 → 노출
- analyze_run D 축 anomaly
- 매 keep commit diff git log 에 남음 (운영자 review 가능)

→ silent disaster 가 아니라 **즉시 보임**. 1 iter cheating 도 catch.

### 3.3 Holdout = 진짜 방어선

- holdout (`AIG_녹취반출_20250813`) chmod 000 + Read deny + `--unseal` 사람 전용
- holdout 평가가 진짜 metric. 0715 eval cheating 은 holdout 에서 즉시 들통
- → cheating 비용 ≫ 이득. LLM 동기 없음.

---

## 4. 판정 — finding 별 결정

| # | 결정 | 이유 |
|---|---|---|
| **C1** Python I/O | **skip (deep fix)** | 25 iter 무공격 + profile 명시 + 즉시 보임 + holdout. tail risk. 부분 차단 (regex) = security theater, 완전 차단 = runtime sandbox 1+ d |
| **C2** atexit poison | **skip** | C1 과 동일 |
| **C3** post-static mutation | **fix 다음번에** | SHA 비교 30 분, 깨끗. 운영 invariant ("commit = verified") 정합성 측면에서도 가치 |
| **C4a** Grep/Glob/LS | **fix 다음번에** | 5 분, defense in depth |
| **C4b** merge 버그 | **fix 다음번에** | 명백한 내 코드 버그, 15 분 |
| C4c wrapper | skip | docstring 명시 충분 |
| **I1** README `--job-id` | **fix 다음번에** | 운영 실수 유발, 5 분 |
| **I2** verify.sh 약함 | **fix 다음번에** | 운영자 preflight 정확성, 15 분 |
| I3/I4 | skip | cosmetic |

---

## 5. 진입 결정

### 지금 phase3_002 50 iter 그대로 진입

근거:
1. codex 위협 모델 (adversarial user) ≠ 우리 context (LLM candidate, profile 협조)
2. phase3_001 25 iter 무공격 실적
3. cheating 즉시 가시 + holdout 진짜 방어선
4. 1+ d 투자해서 발생 안 한 위협 막는 건 ROI 음수

### 다음 잡 전 보강 (이번 결과 보고 결정)

phase3_002 결과가 *정상* (CER > 0.05, per_file 분포 자연스러움) 이면:
- 1 h 10 분 보강 (C4b, I1, C4a, C3, I2) 후 phase4 진행

phase3_002 결과가 *의심스러우면* (CER < 0.05, per_file 완벽 매칭, diagnosis
anomaly) 면:
- 즉시 중단 + commit review
- runtime sandbox (C1/C2 deep fix, 1+ d) 본격 진행
- phase3_003 으로 재실행

---

## 6. 후속 작업 트랙

다음 잡 전 보강 list (총 ~1 h 10 분):

- [ ] C4b — `_harden_candidate_cmd` 의 `--disallowedTools` merge 로직 (15 분)
- [ ] I1 — README §3 에 `--job-id phase3_002` 명시 (5 분)
- [ ] C4a — `.claude/settings.json` 에 `Grep / Glob / LS` deny + `data/raw/label/AIG_녹취반출_20250715/**` Read deny (5 분)
- [ ] C3 — `run_verify` 에 workspace SHA pre/post 비교, 다르면 reject + rollback (30 분)
- [ ] I2 — `scripts/verify.sh` 가 `python -m harness.verify` 위임 (15 분)

phase3_002 결과 보고 deep fix 결정:
- [ ] (조건부) C1/C2 runtime sandbox — transcribe IPC 격리 또는 seccomp (1+ d)

---

## 7. 참고

- 원본 codex review: [`2026-05-29-1931-phase3-002-preflight-review.md`](2026-05-29-1931-phase3-002-preflight-review.md)
- candidate context 정본: [`../CANDIDATE-CONTEXT.md`](../CANDIDATE-CONTEXT.md)
- A' RFC: [`../proposals/2026-05-29-agent-design.md`](../proposals/2026-05-29-agent-design.md)
- holdout 봉인: `scripts/seal_holdout.sh`, `scripts/evaluate_holdout.py`
