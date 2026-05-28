# Phase 3 — Autoresearch 실행 + 분석 플랜

> **범위**: Phase 2 (평가 인프라) DoD 가 전부 통과한 직후 진입.
> baseline + σ + 분석 도구가 준비된 상태에서 가드레일 일괄 활성화 후 `/autoresearch`
> 루프에 `workspace/transcribe.py` 의 진화를 맡긴다. 잡 종료 후 Phase 2 의 분석 도구
> 로 평가한다.

전제: Phase 1 산출물(`judge/`, `scripts/verify.sh`, `baseline/*.json`) + Phase 2
산출물(`scripts/analyze_run.py`, `scripts/evaluate_holdout.py`, REPORT 템플릿) 이
존재하고 정상 동작.

---

## 1. 진입 직전 액션 — 가드레일 일괄 활성화

### 1.1 Holdout 물리 차단 (`scripts/seal_holdout.sh`)

```bash
chmod -R 000 data/raw/wav/AIG_녹취반출_20250813
chmod -R 000 data/raw/label/AIG_녹취반출_20250813
```

잡 종료 후 §6 의 절차로 복구.

### 1.2 Verify 가드 hard-fail 추가

`scripts/verify.sh` 에 다음 임계를 추가 (초기값 — 실험 보며 조정):

| 가드 | 임계 | 위반 시 |
|------|------|----------|
| `hallucination_hit_rate` | `> 0.05` | exit 1 → ROLLBACK |
| `empty_output_rate` | `> 0.10` | exit 1 |
| `length_ratio.p05` | `< 0.3` | exit 1 (출력 너무 짧음) |
| `length_ratio.p95` | `> 3.0` | exit 1 (출력 너무 김) |
| `audio_coverage_rate` | `< 0.8` | sidecar telemetry 가 있을 때만 exit 1 |
| `repeated_text_rate` | `> 0.20` | exit 1 |
| **정적 backend 보호** | `workspace/transcribe.py` 가 `import ctranslate2.models` / `from_pretrained` / `ctranslate2.models.Whisper(` 등 backend 직접 로드 패턴 포함 | exit 1 (frozen 우회 시도) |

`corpus_cer` 자체는 *메트릭으로만 출력* — keep/discard 판정은 autoresearch 가 한다.
가드 위반은 점수 무관 즉시 ROLLBACK.

> **검토 필요**: autoresearch 가 `Δcer ≥ 2σ` 노이즈 임계를 자체 지원하는지 확인.
> 지원 안 하면 verify 가 이전 best metric 을 읽어 노이즈 이하 변화면 exit 1 처리하는
> 방안 추가.

### 1.3 사전 smoke

가드가 실제로 작동하는지 의도적 위반으로 1회 검증:
- `transcribe()` 가 빈 문자열만 반환하도록 임시 패치 → `empty_output_rate = 1.0` → exit 1
- 원복 후 정상 1 iter 동작 확인

---

## 2. autoresearch 호출

Claude Code 세션 안에서:

```
/autoresearch
Goal: workspace/transcribe.py 의 transcribe(audio, sr) 함수를 진화시켜 0715 12 페어 corpus_cer 을 baseline/target_cer.json 의 target_cer 이하로 낮춘다. 어떤 backend·model 변경도 금지 (STT-PIPELINE-SPEC.md §2, §11 참조).
Scope: workspace/transcribe.py
Metric: corpus_cer (lower is better)
Verify: bash scripts/verify.sh
Iterations: 25
```

외부 shell wrapper 형태 아님 — Claude Code 가 자기 자신을 루프 컨트롤러로 사용.

선택: `/autoresearch:plan` 을 한 번 돌려 위 4 종 입력을 검증·구체화한 뒤 본 루프 진입.

---

## 3. 에이전트 노출/숨김

| 노출 | 숨김 |
|------|------|
| `docs/STT-PIPELINE-SPEC.md` (도메인 명세) | `baseline/target_cer.json` 본문 (숫자만 noted) |
| `workspace/transcribe.py` (편집 대상) | `judge/` 본문 (평가자 보호) |
| 자기 `runs/<hyp_id>/score_report.json` 결과 | holdout 디렉토리 (chmod 차단) |
| 자기 `runs/<hyp_id>/per_file.jsonl`, `_telemetry/` 결과 | baseline 산출 코드와 봉인 파일 본문 |

> **검토 필요**: autoresearch 의 파일 접근 권한 제어 메커니즘 확인. 못 막으면 명세
> 텍스트 + 권한(chmod) + 신뢰 모델 조합으로 운영.

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
- `runs/<hyp_id>/_telemetry/*.jsonl` — sidecar (있을 때)
- workspace 변경: autoresearch 가 자체 git commit / revert

---

## 6. 종료 후 절차

### 6.1 종료 조건

- `corpus_cer ≤ target_cer` 달성 → SUCCESS (자동 종료)
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

산출: 8 개 축 (A~H) 의 자동 산출 부분 (cer 추이, 채택률, 가드 위반율, attribution,
diversity 카테고리 분포, 비용). 사람 판단 항목은 템플릿 빈칸으로 남는다 — Phase 2
의 REPORT 템플릿 참조.

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

- [ ] holdout chmod 적용 확인 (`ls data/raw/wav/AIG_녹취반출_20250813` → permission denied)
- [ ] verify 가드 hard-fail 동작 확인 (의도적 위반 케이스 smoke 통과)
- [ ] autoresearch 1 iter 정상 종료 확인 (dry run)
- [ ] 25 iter 완주 또는 target 도달
- [ ] `analyze_run.py` 로 REPORT.md 생성
- [ ] holdout 복구 + 평가 1 회 완료 (`evaluate_holdout.py`)
- [ ] REPORT.md 의 사람 판단 칸 (B 우회 시도, C 추론 품질, D 카테고리 분포, H reasoning 일치) 작성

---

## 8. 열린 검토 항목

- autoresearch 의 `Δcer ≥ 2σ` 노이즈 임계 지원 (§1.2)
- autoresearch 의 파일 접근 권한 제어 메커니즘 (§3)
- 25 iter 총 소요 시간 (Phase 1 verify 측정값 기준 추정 후 확정)
- backend 보호 계층(`frozen/asr_backend.py`) 도입 여부 — 도입 시 Phase 3 진입 *전*
  workspace 와 분리

---

## 9. 안티 패턴

- target 도달 전에 baseline/target_cer.json 갱신
- σ 가 너무 낮다고 (= 결정론) 노이즈 임계 자체를 제거
- 잡 도중 holdout 에 접근 (chmod 우회 포함)
- 가드 임계를 verify 가 출력한 값에 맞춰 *사후* 조정
- autoresearch 의 자체 commit 위에 사람이 수동 commit 끼워 넣기 (히스토리 꼬임)
