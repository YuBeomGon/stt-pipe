# Phase 3 — Autoresearch 실행 + 분석 플랜

> **범위**: Phase 2 (평가 인프라) DoD 가 전부 통과한 직후 진입.
> baseline + σ + 분석 도구가 준비된 상태에서 가드레일 일괄 활성화 후 `/autoresearch`
> 루프에 `workspace/transcribe.py` 의 진화를 맡긴다. 잡 종료 후 Phase 2 의 분석 도구
> 로 평가한다.

전제:
- Phase 1 산출물 (`judge/`, `scripts/verify.sh`, `baseline/*.json`)
- Phase 2 산출물 (`scripts/analyze_run.py`, `scripts/evaluate_holdout.py`, REPORT 템플릿)
- [`docs/AUTORESEARCH.md`](AUTORESEARCH.md) — autoresearch 정본 (정체·동작·가드 함의).
  본 문서 §1·§3·§8 은 AUTORESEARCH.md 의 사실을 전제로 작성됨. 핵심: autoresearch
  의 `Scope` 는 prompt-only — sandbox 가 아니다. 9가지 자체 훅은 일반 안전만 다루고
  본 프로젝트의 scope (workspace/transcribe.py 만 편집) 는 보호하지 않는다. 또한
  `AR_DISABLE_*` ENV 한 줄로 우회 가능 → 1차 layer 로 의존 X.

---

## 1. 진입 직전 액션 — 가드레일 일괄 활성화

> **순서** (모두 사람 수동, 1줄씩):
>
> 1. §1.1 verify.sh swap → Phase 3 본문 활성
> 2. §1.2 .claude swap → 프로젝트 PreToolUse 가드 + autoresearch 설치 확인
> 3. §1.3 holdout 물리 차단 (chmod 000)
> 4. §1.5 사전 smoke — 가드 동작 의도적 위반 검증
> 5. `/autoresearch` 호출 (§2)
>
> §1.4 는 판정 정책 표 — 실행 단계가 아닌 *참조*. 위 순서에 포함되지 않음.

### 1.1 `verify.sh` swap (사람 전용)

평소 `scripts/verify.sh` 는 Phase 1·2 미니멀 본문 (가드 OFF). Phase 3 본문은
`scripts/verify.sh.alt` 에 보관. 사람이 다음 1줄로 둘을 교환:

```bash
bash scripts/swap_verify.sh
head -3 scripts/verify.sh    # "Phase 3 verify" 헤더 확인
```

다시 Phase 1·2 로 돌아갈 때도 같은 명령 한 번 더 → 원복.

> **에이전트 호출 금지**: `scripts/swap_verify.sh` 는 `judge/` 와 동급
> 보호 대상이다 (AGENTS.md §1 표). 어떤 에이전트도 (Claude, autoresearch,
> 보조 스크립트 포함) 본 스크립트를 직접 실행하지 않는다.

### 1.2 `.claude` swap (사람 전용) — 프로젝트 PreToolUse 가드 활성화

[`AUTORESEARCH.md §6`](AUTORESEARCH.md) 의 결론: autoresearch 의 `Scope` 는
prompt 일 뿐, Claude Code 의 Edit/Write 도구를 Scope 밖 파일에 대해 차단해
주지 않는다. 또한 autoresearch 자체의 9가지 훅 ([`AUTORESEARCH.md §7`](AUTORESEARCH.md))
은 일반 안전만 다루고 본 프로젝트의 scope (`workspace/transcribe.py` 만 편집)
는 보호하지 않으며 `AR_DISABLE_*` ENV 로 우회 가능하다.

따라서 **본 프로젝트의 scope 가드는 우리 저장소의 `.claude/`** 가 책임진다.
구성:

- `.claude/settings.json` — `permissions.deny` allowlist (Edit/Write 대상 제한)
- `.claude/hooks/` — PreToolUse 훅으로 `judge/`, `frozen/`, `baseline/`,
  `assets/`, `scripts/swap_verify.sh`, `scripts/seal_holdout.sh`,
  `scripts/evaluate_holdout.py` 편집 시도 거부. ENV 토글로 끄지 *못하게* 구현.
- `.ckignore` — scout-block 확장 (읽기 차단). holdout / baseline / judge /
  frozen / audio_profile 패턴 추가. (편집 차단은 위 PreToolUse 훅이 책임,
  `.ckignore` 는 컨텍스트 채우기 방지용.)
- PostToolUse 훅 — `workspace/transcribe.py` 편집 후 verify 자동 호출 (옵션)

평소 (Phase 1·2) 우리 저장소의 `.claude/` 는 가드 OFF (또는 *부재*) 본문.
Phase 3 본문은 `.claude.alt/` (또는 동등 구조) 에 보관. 사람이 1줄로 교환:

```bash
bash scripts/swap_claude.sh
ls .claude/hooks/                # 활성 본문에 hooks 디렉토리 있는지 확인
```

다시 Phase 1·2 로 돌아갈 때도 같은 명령 한 번 더 → 원복.

> **에이전트 호출 금지**: `scripts/swap_claude.sh` 도 `scripts/swap_verify.sh`
> 와 동급. 사람 전용 (AGENTS.md §1 표).
>
> **autoresearch 본체 설치는 별도**: 사람이 `npx skills add uditgoenka/autoresearch`
> 로 ~/.claude/ 또는 프로젝트 .claude/ 에 autoresearch 의 commands/skills/hooks
> 를 설치. 본 §1.2 의 우리 저장소 `.claude/` 자산은 *그 위에 얹는 프로젝트 scope
> 가드* 다. 설치 위치 (글로벌 vs 프로젝트) 와 우리 가드 자산이 충돌하지 않는지는
> 자산 작성 시 검증.
>
> **상태**: `.claude/` / `.claude.alt/` / `scripts/swap_claude.sh` 자산 자체는
> 본 문서 갱신 시점에 *미작성*. 작성은 본 §1.2 의 후속 작업이며 `phase3`
> 브랜치에서 진행된다 (verify 가드와 동일 패턴).

### 1.3 Holdout 물리 차단 (`scripts/seal_holdout.sh`)

```bash
chmod -R 000 data/raw/wav/AIG_녹취반출_20250813
chmod -R 000 data/raw/label/AIG_녹취반출_20250813
```

잡 종료 후 §6 의 절차로 복구.

### 1.4 Verify 판정 정책

Phase 3 의 최종 목표점은 사람이 정한 `target_cer` (현재 `0.10`) 다. faster-whisper
`baseline_cer` 은 그 목표까지 거리를 보기 위한 참조 앵커일 뿐 성공 기준이 아니다
(STT-PIPELINE-SPEC §7 / DESIGN §2.9). 중간 iteration 은 이 목표를 매번 넘겨야 하는
것이 아니라, 현재 best 대비 `2σ` 이상 개선되는지를 보고 keep/revert 한다.

- 최종 CER 목표: `baseline/target_cer.json:target_cer` (= 0.10, 수동)
- 최종 시간 목표: `baseline/target_cer.json:total_inference_time_s` (faster-whisper 측정값)
- 비교 앵커: `baseline/target_cer.json:baseline_cer` (faster-whisper corpus_cer)
- 중간 keep/revert 기준: 현재 best 대비 `baseline/noise_floor.json:sigma`
- 품질 참조값: baseline 측정 때 같은 judge 로 산출한 guard 분포

`scripts/verify.sh` 는 너무 많은 품질 지표를 전부 hard-fail 로 만들지 않는다.
hard-fail 은 “무효 후보”만 즉시 rollback 하고, hallucination/length/repetition/coverage 는
baseline 목표 대비 큰 악화 여부와 diagnosis 로 다룬다.

| 층위 | 판정 | 위반 시 |
|------|------|----------|
| **정적 backend 보호** | `workspace/transcribe.py` 에 `import ctranslate2` / `import transformers` / `from_pretrained` / `Whisper(` 중 어느 패턴이라도 출현 | exit 1 |
| **정적 profile 직접참조 차단** | `workspace/transcribe.py` 에 `assets` / `audio_profile` / `silero` 중 어느 substring 이라도 출현 (case-insensitive) | exit 1 |
| **실행 무효** | import 실패, evaluate crash, 마지막 줄 숫자 없음, `score_report.json` 누락 | exit 1 |
| **산술 무결성** | `Σ edits / Σ ref_chars != corpus_cer` 또는 per-file 합산 불일치 | exit 1 |
| **catastrophic output** | `empty_output_rate > 0.50` 또는 `length_ratio.p05 < 0.10` 또는 `length_ratio.p95 > 5.0` | exit 1 |
| **runtime hard cap** | `total_inference_time_s > baseline.total_inference_time_s * RUNTIME_HARD_MULTIPLIER` | exit 1 |
| **quality budget** | hallucination/repetition/coverage/length 가 `baseline_cer` 측정 시 guard 분포 대비 크게 악화 | 기본 warning + diagnosis. 악화 허용폭 초과 시 exit 1 가능 |
| **keep/revert** | `corpus_cer <= best_cer - 2σ` | autoresearch 가 keep, 아니면 rollback |
| **success** | `corpus_cer <= target_cer` 그리고 `total_inference_time_s <= baseline.total_inference_time_s * RUNTIME_SUCCESS_MULTIPLIER` | 종료 가능 |

초기 운영값:
- `RUNTIME_HARD_MULTIPLIER=3.0` — 폭주 방지용. 너무 빡빡하면 탐색 자체가 막힘.
- `RUNTIME_SUCCESS_MULTIPLIER=1.0` — 시간 목표는 faster-whisper time 이하 (앵커가 baseline_cer 측정값).
- `target_cer=0.10` — 사람이 정한 도달 목표. faster-whisper 가 그보다 높은 CER 을 내더라도 변경하지 않는다.
- `quality budget` 은 baseline guard 값 + 작은 허용폭으로 시작하되, Phase 1 baseline 측정값을 보고 확정.

`corpus_cer`, runtime, guard 값은 모두 `score_report.json` 에 기록한다. 에이전트의 원인
추론은 `diagnosis_report.json` 을 본다.

> **검토 필요**: autoresearch 가 `Δcer ≥ 2σ` 노이즈 임계를 자체 지원하는지 확인.
> 지원 안 하면 verify 가 이전 best metric 을 읽어 노이즈 이하 변화면 exit 1 처리하는
> 방안 추가.

### 1.5 사전 smoke

가드가 실제로 작동하는지 의도적 위반으로 1회 검증 (사람 수동):

```bash
# (a) catastrophic output 가드 — transcribe() 가 빈 문자열만 반환하도록 임시 패치
#     workspace/transcribe.py 본문을: def transcribe(audio, sr): return ""
bash scripts/verify.sh        # → exit 1, "catastrophic output" 메시지
git checkout -- workspace/transcribe.py

# (b) 정적 backend 가드 — workspace 상단에 `import ctranslate2` 추가
#     bash scripts/verify.sh  # → exit 1, "static backend" 메시지
#     git checkout -- workspace/transcribe.py

# (c) .claude PreToolUse 가드 — judge/ 또는 baseline/ 또는 frozen/ 안 파일을
#     Claude Code 세션에서 Edit 시도. 훅이 거부해야 함. (스크립트로 자동화하기
#     어려우니 사람이 직접 시도)

# (d) 정상 1 iter
bash scripts/verify.sh        # → exit 0, 마지막 줄에 corpus_cer 한 숫자
```

(a)·(b)·(c) 까지 확인하면 가드 ON 상태 신뢰 가능. 그 다음 §1.3 holdout seal →
`/autoresearch`.

---

## 2. autoresearch 호출

### 2.1 호출 직전 사전조건 체크리스트 (사람)

본 invocation 을 입력하기 *전* 에 모두 ✓ 인지 확인:

```bash
# (a) verify.sh 가 Phase 3 본문 활성
head -3 scripts/verify.sh        # → "# Phase 3 verify (PHASE3-PLAN.md §1.2)."

# (b) .claude/ 가 Phase 3 본문 활성
ls .claude/                       # → settings.json + hooks/ + README.md
ls .claude/hooks/                 # → restrict_workspace.py + block_swap_and_seal.py

# (c) holdout 봉인
stat -c '%a' data/raw/wav/AIG_녹취반출_20250813   # → 0
stat -c '%a' data/raw/label/AIG_녹취반출_20250813 # → 0

# (d) autoresearch 본체 설치 — 다음 둘 중 하나 통과
ls ~/.claude/skills/autoresearch/SKILL.md   # 글로벌
ls .claude/skills/autoresearch/SKILL.md     # 프로젝트
# (없으면: `npx skills add uditgoenka/autoresearch` 또는
#  `/plugin marketplace add uditgoenka/autoresearch`)

# (e) Python 환경 활성
echo "$CONDA_DEFAULT_ENV"          # → py11 (또는 동등 env, 3.11+)

# (f) 작업 브랜치 (autoresearch 가 매 iter `experiment:` 커밋을 남기므로)
git rev-parse --abbrev-ref HEAD    # → phase3 (또는 main 이 아닌 작업 브랜치)

# (g) 사전 smoke 통과 (§1.5 a·b·c)

# (h) AR_DISABLE_* 환경변수 미설정
env | grep ^AR_DISABLE_             # → (출력 없음)
```

하나라도 미통과면 §1 의 해당 단계 재실행.

### 2.2 (선택) `/autoresearch:plan` 우선 실행

AUTORESEARCH.md §3·§4 에 따르면 `/autoresearch:plan` 이 Goal → validated
Scope/Metric/Verify config 로 변환한다 (산출: `handoff.json`). 본 루프 진입 *전*
1 회 권장. plan 결과가 §2.3 의 invocation 과 의미상 일치하는지 사람이 확인.

### 2.3 `/autoresearch` invocation

Claude Code 세션 안에서:

```
/autoresearch
Goal: workspace/transcribe.py 의 transcribe(audio, sr) 함수를 진화시켜 0715 11 페어 corpus_cer 을 baseline/target_cer.json 의 `target_cer` (= 0.10, 수동 목표) 이하로 낮추고, total_inference_time_s 는 같은 파일의 baseline time budget 안에 둔다. faster-whisper `baseline_cer` 은 비교 앵커일 뿐 그 값을 넘기는 게 성공 기준은 아니다. 어떤 backend·model 변경도 금지 (STT-PIPELINE-SPEC.md §2, §7, §11 참조).
Scope: workspace/transcribe.py
Metric: corpus_cer (primary, lower is better); runtime and quality budget are verify constraints
Verify: bash scripts/verify.sh
Iterations: 25
```

외부 shell wrapper 형태 아님 — Claude Code 가 자기 자신을 루프 컨트롤러로 사용.

루프 구조 그림은 [`PHASE3-LOOP.md`](PHASE3-LOOP.md) 를 참조. autoresearch 의 자체
루프 알고리즘과 결과 저장 위치 (`autoresearch/<sub>-<YYMMDD>-<HHMM>/*.tsv`)
는 [`AUTORESEARCH.md §5·§9·§10`](AUTORESEARCH.md) 참조. 본 프로젝트의 분석 정본은
autoresearch 의 TSV 가 아니라 우리 `runs/<hyp_id>/` 산출이다.

---

## 3. 에이전트 노출/숨김

| 노출 | 숨김 |
|------|------|
| `docs/STT-PIPELINE-SPEC.md` (도메인 명세) | `baseline/target_cer.json` 본문 (숫자만 noted) |
| `workspace/transcribe.py` (편집 대상) | `judge/` 본문 (평가자 보호) |
| 자기 `runs/<hyp_id>/score_report.json` 결과 | holdout 디렉토리 (chmod 차단) |
| 자기 `runs/<hyp_id>/per_file.jsonl`, `_telemetry/` 결과 | baseline 산출 코드와 봉인 파일 본문 |
| 자기 `runs/<hyp_id>/diagnosis_report.json` — 11파일 profile summary + focus file 최대 2개 | `assets/audio_profile/` 원본 (workspace 직접 참조 금지) |

> **권한 모델 — 결론** (AUTORESEARCH.md §6 참조): autoresearch 자체는 파일 접근
> 권한 제어를 *하지 않는다*. Scope 는 prompt 일 뿐. 본 표의 "숨김" 열은 다음
> 4개 레이어로 강제된다:
>
> 1. OS chmod 000 — holdout (0813) 디렉토리
> 2. `scripts/verify.sh` (Phase 3 본문) 의 정적 grep — workspace 안 backend /
>    profile 직접참조 차단 (§1.4 표 1·2 행)
> 3. 우리 저장소 `.claude/` 의 PreToolUse 훅 (§1.2 참조) — judge/, frozen/,
>    baseline/, assets/, 보호 scripts 편집 거부
> 4. `.ckignore` — 컨텍스트 채우기 방지 (읽기 차단, 편집 차단 아님)
>
> autoresearch 의 9가지 자체 훅 (§7 of AUTORESEARCH.md) 은 일반 안전만 다루며
> 본 표의 scope 보호를 *대체하지 않는다*. ENV 한 줄 우회 가능하므로 1차 layer
> 로 의존 X.

---

## 4. 운영

### 4.1 모니터링

- `runs/` 디렉토리 — 새 `<hyp_id>/` 가 매 iter 추가
- `git log --oneline` — autoresearch 가 만든 commit / revert 흔적
- 각 iter 마지막의 corpus_cer 진척

### 4.2 중단

세션 종료 또는 `Esc` — 진행 중 iter 은 verify 가 끝나면 자연 중단. 다음 세션에서
`/autoresearch` 재호출 시 `--continue` 형태 지원 여부는 autoresearch 측 문서 참조.

### 4.3 무진전 대응

10 iter 이상 가드 위반만 반복 또는 corpus_cer 변동 < σ — 사람이 중단하고 `runs/`
분석. 가드 임계가 너무 빡빡한지, scope 가 너무 좁은지 검토.

---

## 5. 산출물 위치

각 iteration:
- `runs/<hyp_id>/score_report.json` — 메트릭 + 가드
- `runs/<hyp_id>/per_file.jsonl` — 진단 (per-file telemetry)
- `runs/<hyp_id>/diagnosis_report.json` — LLM 추론용 11파일 summary + focus file 최대 2개
- `runs/<hyp_id>/_telemetry/*.jsonl` — sidecar (있을 때)
- workspace 변경: autoresearch 가 자체 git commit / revert

---

## 6. 종료 후 절차

### 6.1 종료 조건

- `corpus_cer ≤ target_cer` 그리고 time budget 만족 → SUCCESS (자동 종료)
- 25 iter 소진 → 종료, 결과 분석
- 무한 가드 위반 / 무진전 → 사람이 중단

### 6.2 분석 — Phase 2 도구 적용

잡 종료 직후 (holdout 복구 *전*):

```bash
# 1. 잡 종료 마커 — evaluate_holdout 잠금 해제용
mkdir -p runs/_summary
touch runs/_summary/JOB_DONE.lock

# 2. 분석 리포트 생성
python scripts/analyze_run.py \
  --runs-dir runs/ \
  --baseline baseline/ \
  --template docs/templates/REPORT.md \
  --out runs/_summary/REPORT.md
```

산출: 8 개 축 (A~H) 의 자동 산출 부분 (cer 추이, 채택률, 가드 위반율, diagnosis
focus file 추이, attribution, diversity 카테고리 분포, 비용). 사람 판단 항목은
템플릿 빈칸으로 남는다 — Phase 2 의 REPORT 템플릿 참조.

### 6.3 Holdout 복구 + 수동 평가

분석 완료 후 *사람이 1 회만* (`--unseal` 명시 필요):

```bash
python scripts/evaluate_holdout.py --unseal
# 내부 동작:
#   0. runs/_summary/JOB_DONE.lock 확인 (없으면 거부)
#   1. holdout chmod 복구 (u+rwX)
#   2. 0813 13 페어 evaluate
#   3. 0715 결과와 비교 → runs/_summary/HOLDOUT.md 산출
#   4. holdout chmod 000 으로 재봉인 (재호출 차단)
```

일반화 확인 = `corpus_cer` 이 0715 결과와 크게 다르지 않은지. 차이 크면 0715 에
overfit 된 신호.

> **잡 도중 어떤 형태로도 호출되면 무효**. evaluate_holdout.py 는 진행 중 잡
> 잠금 파일을 확인하고 거부한다.

---

## 7. Phase 3 DoD

**진입 가드 (자산 작성 + 1 회 동작 검증)**:

- [x] `scripts/swap_verify.sh` 작성 + 3-way mv 무결성 검증 (commit 2497876)
- [x] `scripts/swap_claude.sh` 작성 + 3-way mv 무결성 검증 (commit d91384c)
- [x] `scripts/seal_holdout.sh` idempotent 정정 + find-기반 chmod (commit a0a1f97)
- [x] `.claude.alt/settings.json` permissions.deny 11 종 보호 경로 + hooks 등록
- [x] `.claude.alt/hooks/restrict_workspace.py` (Edit/Write/MultiEdit 가드)
- [x] `.claude.alt/hooks/block_swap_and_seal.py` (Bash 가드)
- [x] `.ckignore` 작성 (autoresearch scout-block 읽기 차단 확장)
- [x] pytest 가드 단위 검증 56/56 (verify_check 9, claude hooks 19, seal 4, 등)

**Phase 3 진입 직전 (사람 1회)**:

- [x] swap_verify 실행 → `head -3 scripts/verify.sh` = "Phase 3 verify (PHASE3-PLAN.md §1.2)."
- [x] swap_claude 실행 → `ls .claude/hooks/` = restrict_workspace + block_swap_and_seal
- [x] holdout chmod 적용 확인 (`stat -c '%a'` = 0, `ls` → Permission denied)
- [x] autoresearch 본체 설치 확인 (글로벌 `~/.claude/`, 2026-05-29). Skill 13 종 (autoresearch + 12 sub) 인식됨
- [ ] `/autoresearch:plan` 입력 검증 1 회 (선택)
- [ ] verify 의도적 위반 smoke 통과 (§1.5 a·b·c)
- [ ] `.claude` PreToolUse 훅 *수동* smoke (judge/baseline/frozen Edit 시도 → 거부)

**잡 실행 + 분석**:

- [ ] autoresearch 1 iter 정상 종료 확인 (dry run)
- [ ] 25 iter 완주 또는 target 도달
- [ ] `analyze_run.py` 로 REPORT.md 생성
- [ ] holdout 복구 + 평가 1 회 완료 (`evaluate_holdout.py --unseal`)
- [ ] REPORT.md 의 사람 판단 칸 (B 우회 시도, C 추론 품질, D 카테고리 분포, H reasoning 일치) 작성

---

## 8. 열린 검토 항목

- autoresearch 의 `Δcer ≥ 2σ` 노이즈 임계 자체 지원 여부 (AUTORESEARCH.md §11
  의 미확인 항목). 미지원 가정으로, verify 가 이전 best metric 을 읽어 노이즈
  이하 변화면 거부하는 보조 가드 추가 필요 여부 검토.
- 25 iter 총 소요 시간 (Phase 1 verify 측정값 기준 추정 후 확정)
- quality budget 허용폭 (baseline guard 분포 측정 후 확정)
- autoresearch 본체 설치 위치 (글로벌 `~/.claude/` vs 프로젝트 `.claude/`) 와
  우리 프로젝트 `.claude/` 자산의 공존 — §1.2 자산 작성 시 검증
- autoresearch 의 `experiment:` 커밋 빈도 / git 히스토리 오염 정도. 잡 종료 후
  squash 정책 필요 여부

**결정됨** (이전 "검토 필요" 에서 옮김):
- autoresearch 의 파일 접근 권한 제어 메커니즘 → AUTORESEARCH.md §6 으로 해소.
  Scope 는 prompt-only. 우리 `.claude/` PreToolUse 훅 + verify 정적 grep + OS
  chmod 의 3중 가드로 대체 (§3 권한 모델 결론 참조).
- frozen layer 도입 완료 (`frozen/asr_backend.py`). Phase 3 진입 §1.5 smoke
  에서 권한 차단 + 정적 import 검사 둘 다 작동 검증.

---

## 9. 안티 패턴

- 에이전트가 `scripts/swap_verify.sh` 또는 `scripts/swap_claude.sh` 호출 (둘 다 사람 전용 — AGENTS.md §1)
- autoresearch 의 9가지 자체 훅 / Scope 만 믿고 `.claude/` PreToolUse 가드 생략
- `AR_DISABLE_*` ENV 로 autoresearch 자체 훅을 우회하는 형태로 운영 — Phase 3 entry 시점에 ENV 미설정 확인
- target 도달 전에 baseline/target_cer.json 갱신
- σ 가 너무 낮다고 (= 결정론) 노이즈 임계 자체를 제거
- 잡 도중 holdout 에 접근 (chmod 우회 포함)
- 가드 임계를 verify 가 출력한 값에 맞춰 *사후* 조정
- autoresearch 의 자체 commit 위에 사람이 수동 commit 끼워 넣기 (히스토리 꼬임)
