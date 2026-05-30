# Review — Phase 2 commit `f6f746a`

검토 대상:
- Base: `ef1dd21` (Phase 1 + P1/P2 정합성 정정 완료)
- Head: `f6f746a` (Phase 2 산출 — 평가 인프라 5 파일)
- 범위: `git diff ef1dd21..f6f746a` — `docs/templates/REPORT.md`,
  `scripts/analyze_run.py`, `scripts/evaluate_holdout.py`,
  `tests/test_analyze_smoke.py`, `tests/test_evaluate_holdout_smoke.py`.
- 코드 수정 없이 리뷰만 수행.

## 결론

Phase 2 DoD (§7) 6 개 체크 항목은 형식적으로 모두 충족. 8 개 축 (A~H) 변수가
template 에 모두 정의돼 있고 `analyze_run.render_report` 의 substitutions dict 와
1:1 대응한다 (leftover 변수 없음, smoke 테스트도 동일 확인). 사람 판단 칸 4 곳
(B/C/D/H) + 종합 결론 칸도 `<!-- TODO -->` 로 보존.

다만 Phase 3 산출물이 본 인프라를 처음으로 실사용하기 *전* 에 고쳐야 할 두 개의
실측 가능한 결함이 있다:

1. `analyze_run` 의 `_EXCLUDED_DIRNAMES` 가 holdout 실제 디렉토리 명명 규칙
   (`holdout_<unix_ts>`) 과 어긋나, holdout 평가 후 `analyze_run` 을 재실행하면
   0813 결과가 0715 iter 시계열에 섞여 들어가 A/C/G 축이 오염된다.
2. `evaluate_holdout` 의 chmod 복구가 `try` *바깥* 에서 일어나, label 디렉토리
   chmod 실패 시 wav 디렉토리가 unsealed 상태로 남는다 — PHASE2-PLAN §8
   안티패턴 "chmod 복구만 하고 재봉인 안 하기" 의 한 변형.

두 건 모두 P1. P0 격상은 보류 — Phase 3 진입 전 사람이 한 번 더 손대는 산출물이고,
명세에 "어떤 holdout 디렉토리 prefix 가 표준" 이라는 정의가 없어 정본 위반은
아니다. 다만 첫 잡 분석 전에는 반드시 손봐야 한다.

이 외에 D 축 (탐색 다양성) 의 한국어 commit 메시지 분류 누락, G 축 recovery
rate 분모 계산 오류 (특정 패턴에서 항상 `n/a`), `enrich_with_git` 의 타임존
처리 등이 잔존 결함.

---

## Findings

### P1 — `analyze_run.discover_iterations` 가 holdout run 디렉토리를 iter 로 오인식

`scripts/analyze_run.py:31` 의 `_EXCLUDED_DIRNAMES = {"_summary", "holdout", "_telemetry"}`
는 정확히 이름이 `"holdout"` 인 디렉토리만 제외한다. 그런데 `evaluate_holdout`
은 holdout 결과를 `runs/holdout_<unix_ts>/` 로 떨군다
(`scripts/evaluate_holdout.py:304` — `out_run_id = args.out_run_id or f"holdout_{int(time.time())}"`).

기대 동작: holdout 평가 후 `analyze_run` 을 다시 돌려도 0715 eval iter 시계열만
분석돼야 한다.

관찰 동작: `child.name in _EXCLUDED_DIRNAMES` 체크는 `"holdout_1700000000" in {"holdout"}`
== `False` 이므로 holdout 결과 디렉토리가 그대로 `IterRecord` 로 적재된다.
score_report 의 `batch` 가 `AIG_녹취반출_20250813` 이므로 per_file_movers / top3 /
guard 분포가 0813 데이터로 오염되고, `produced_at` 이 잡 종료 *이후* 이라 정렬상
시계열 마지막에 끼어 `final_corpus_cer` 가 holdout cer 로 치환될 수 있다.

비교 참고: `evaluate_holdout._last_accepted_eval_run` 은 같은 문제를 의식해
`child.name.startswith("holdout")` 로 거른다 (`scripts/evaluate_holdout.py:94`).
`analyze_run` 만 일관성에서 빠짐.

수정 방향: `discover_iterations` 의 제외 조건을 `name.startswith("holdout")` 또는
`name.startswith("_")` + 명시 이름 합으로 확장. 추가로 score_report 의 `batch`
필드가 `_EVAL_BATCH` 가 아닌 디렉토리를 자동 제외하는 방어층을 두면 안전.

### P1 — `evaluate_holdout` chmod 복구가 `try` 바깥에서 실행돼 부분 unseal 누수 가능

`scripts/evaluate_holdout.py:319-323`:

```python
# 2. chmod 복구
if wav_dir.exists():
    _chmod_recursive(wav_dir, 0o755)
if label_dir.exists():
    _chmod_recursive(label_dir, 0o755)
log.info("holdout dirs unsealed: %s, %s", wav_dir, label_dir)

try:
    # 3. evaluate ...
finally:
    # 5. 재봉인
```

기대 동작: 어떤 단계에서 예외가 터지든 holdout 두 디렉토리가 무조건 0o000 으로
재봉인. PHASE2-PLAN §8 안티패턴 마지막 항목 "evaluate_holdout.py 에서 chmod 복구만
하고 재봉인 안 하기" 를 방지.

관찰 동작: wav chmod 0o755 성공 직후 label chmod 가 `PermissionError` /
`CalledProcessError` (예: 외부 마운트 / 부모 디렉토리 권한 / subprocess `check=True`
실패) 로 throw 하면, `try` 진입 *전* 이라 `finally` 가 실행되지 않는다 →
**wav_dir 만 0o755 로 남고 스크립트 종료**. 다음 호출 시 `_chmod_recursive(wav, 0o755)`
는 또 succeed → `judge.evaluate` 가 데이터를 읽어 다시 평가 가능. 즉 1 회 평가
제약이 무너진다.

수정 방향: chmod 복구를 `try` 블록 *안* 으로 끌고 들어가, `finally` 가
재봉인 책임을 잡게 한다. 또는 unseal 직후부터 cleanup 보장하는 wrapper
(`with contextlib.ExitStack`) 로 묶는다.

```python
try:
    if wav_dir.exists():
        _chmod_recursive(wav_dir, 0o755)
    if label_dir.exists():
        _chmod_recursive(label_dir, 0o755)
    log.info("holdout dirs unsealed")
    # ... evaluate / report ...
finally:
    if wav_dir.exists():
        _chmod_recursive(wav_dir, 0o000)
    if label_dir.exists():
        _chmod_recursive(label_dir, 0o000)
```

### P1 — G 축 `recovery_rate_pct` 의 분모 계산이 특정 시퀀스에서 항상 0 → `n/a`

`scripts/analyze_run.py:605-626` `_recovery_and_streak`:

```python
for it in iters:
    if it.accepted:
        if prev_rollback:
            n_recovered += 1
        ...
        prev_rollback = False
    else:
        if prev_rollback:
            n_after_rollback += 1
        else:
            n_after_rollback += 0  # noop; counted on next accept
        ...
        prev_rollback = True
rec_rate = (n_recovered / n_after_rollback * 100) if n_after_rollback else 0.0
```

기대 동작 (PHASE2-PLAN §3.3 G): "ROLLBACK 직후 CONTINUE 비율". 즉 각 rollback
"에피소드" 가 직후 accept 로 recover 했는지 비율. 시퀀스 `[A, R, A, R, A]` 의
recovery rate 는 2/2 = 100%.

관찰 동작: 위 시퀀스를 트레이스하면 `n_after_rollback = 0`, `n_recovered = 2`.
함수가 반환하는 첫 값이 `"n/a" if not n_after_rollback else ...` 이므로 결과는
`"n/a"`. 즉 길이 1 rollback 만 있는 잡은 회복률이 무조건 `n/a`. 두 길이 1 +
한 길이 2 가 섞이면 회복률이 1/2 = 50% 인데, 정의대로면 2/3 = 67% 여야 한다.

코드의 의도는 주석 `"# noop; counted on next accept"` 로 미루어 "다음 accept 에서
세어주자" 인데, 그 "다음 accept" 분기는 단지 `n_recovered += 1` 만 할 뿐 분모는
영원히 증가하지 않는다. 누락된 분기.

수정 방향: rollback 에피소드 = accept→rollback 전이의 *총 횟수*. 가장 단순한
교정:

```python
prev_accepted = True  # virtual "before-first" state
n_rollback_episodes = 0
n_recovered = 0
prev_was_rollback = False
for it in iters:
    if it.accepted:
        if prev_was_rollback:
            n_recovered += 1
        prev_was_rollback = False
    else:
        if not prev_was_rollback:  # transition into rollback
            n_rollback_episodes += 1
        prev_was_rollback = True
        ...
rec_rate = n_recovered / n_rollback_episodes if n_rollback_episodes else None
```

부차적 의문: 잡이 rollback 으로 *끝난* 마지막 에피소드는 분모에 들어가나 분자에는
안 들어간다 — 의도에 맞는다 (회복 못 함). 명세화 필요시 docstring 명시.

### P1 — D 축 카테고리 분류가 한국어 commit 메시지에 작동하지 않음

`scripts/analyze_run.py:35-40` 의 `_CATEGORY_PATTERNS` 는 영어 키워드만 포함
(`chunk|window|split|segment` 등). 그런데 같은 파일 `_REASONING_RULES`
(`scripts/analyze_run.py:46-61`) 는 한국어 키워드 (`환각`, `삽입`, `누락`, `반복`,
`길이`) 를 모두 추가했다 — 즉 같은 모듈 안에서 다국어 정책이 비대칭.

기대 동작 (PHASE2-PLAN §3.3 D): autoresearch 가 채택한 commit 들의 카테고리
분포가 잡혀야 한다. CLAUDE.md / AGENTS.md 가 한국어 운영문서고 autoresearch
프롬프트도 한국어 중심이라 채택 commit 메시지가 한국어인 경우가 흔하다.

관찰 동작: "청크 윈도우 보강", "프롬프트 한국어 hint 추가", "후처리 dedup",
"beam search 변경" 같은 메시지 중 마지막 둘만 매칭. 한국어 메시지는 대부분
`unclassified` 로 빠져 카테고리 분포가 무력화 → "80% 쏠림 경고" 도 무력화.

수정 방향: 각 카테고리에 한국어 동의어 추가. PHASE2-PLAN 본문에 적힌 영문
키워드는 *예시* 라는 점을 반영해 docstring 보강:

```python
_CATEGORY_PATTERNS = {
    "chunking": re.compile(
        r"(\bchunk|\bwindow|\bsplit|\bsegment|청크|윈도우|분할|세그먼트)",
        re.IGNORECASE,
    ),
    "prompt": re.compile(
        r"(\bprompt|\btoken|\blanguage|프롬프트|토큰|언어)",
        re.IGNORECASE,
    ),
    "decode": re.compile(
        r"(\bbeam|\btemperature|\bfallback|\bsample|빔|온도|샘플|폴백)",
        re.IGNORECASE,
    ),
    "post": re.compile(
        r"(\bdedup|\bmerge|\bregex|\bpostprocess|후처리|중복|병합)",
        re.IGNORECASE,
    ),
}
```

D 축의 사람 칸 (수동 재분류) 이 있으니 자동 분류가 어긋나도 치명적이지는
않지만, "자동 분류 → 사람이 수정" 의 작업량을 늘리는 비효율.

### P1 — `enrich_with_git` 타임존 비교가 비-UTC 호스트에서 어긋남

`scripts/analyze_run.py:182-221` `enrich_with_git`:

```python
target = it.produced_at
if target.tzinfo is not None:
    target = target.replace(tzinfo=None)
best: tuple[str, datetime, str] | None = None
for sha, ts, subject in commits:
    if ts <= target and (best is None or ts > best[1]):
        best = (sha, ts, subject)
```

여기서 `it.produced_at` 은 `score_report.json` 의 `produced_at` ISO 문자열을 파싱한 결과
이며 `judge/evaluate.py:157` 의 `datetime.now(UTC).isoformat()` 이므로 UTC tz-aware.
`replace(tzinfo=None)` 으로 UTC-naive 가 된다.

반면 `commits` 의 `ts` 는 `datetime.fromtimestamp(int(ts))` — `%ct` (commit unix
timestamp) 를 **로컬 타임존** 으로 변환한 naive datetime.

기대 동작: produced_at (UTC) 와 commit ts (UTC) 비교.

관찰 동작: produced_at-naive 는 UTC, commit ts-naive 는 local (예: KST = UTC+9).
KST 호스트에서 같은 순간의 두 값은 9 시간 차이 → "직전 commit" 매칭이 한국 시간
기준 9 시간 정도 뒤로 밀려 잘못된 sha 와 매칭. 동일 잡 안에서 iter ↔ commit
1:1 정렬이 보장 안 됨.

수정 방향: `datetime.fromtimestamp(int(ts), tz=UTC)` 로 만들고 `target` 도
UTC tz-aware 그대로 비교. 또는 `int(ts) <= target.timestamp()` 로 unix sec 비교
(둘 다 epoch 정의가 동일하므로 안전).

부차: `it.commit_ts` 가 sidecar 분기에서는 **set 되지 않는다** (line 211 `continue`
직전에 `commit_ts = None` 인 채로 두고 다음 iter 로). 현재 어떤 다운스트림 코드도
`commit_ts` 를 안 읽지만, 미래에 사용 시 silent None.

### P1 — `evaluate_holdout._read_json` 가 JSON 파싱 실패를 raise (운영 중 1 회 호출에서 크래시)

`scripts/evaluate_holdout.py:36-39`:

```python
def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
```

`analyze_run._read_json` 은 `json.JSONDecodeError` 를 warn 으로 처리하고 None 반환
(`scripts/analyze_run.py:122-129`). evaluate_holdout 만 raise.

기대 동작: holdout 평가는 "잡 끝나고 1 회 한정" 의 사고-제일-방지 코드 경로
이므로, 부차적 JSON (eval_score, target_cer, noise_floor) 파싱 실패 시에도
**unseal/re-seal 시퀀스는 빠져나갈 수 있어야** 한다. raise 가 try 바깥에서
일어나면 (예: target_cer.json 깨졌을 때 line 299) finally 진입 전이므로 #2 와
같은 누수 (현재는 chmod 복구 전이라 디렉토리는 봉인된 채이므로 안전 — 다만
chmod 복구 후 line 334 `eval_score = _read_json(eval_run / ...)` 에서 raise 하면
finally 는 도달하지만 사용자에게 trace 만 남고 부분 분석 산출 X).

수정 방향: `try/except json.JSONDecodeError` 로 감싸 None 반환 + 로그. 일관성과
운영 신뢰성 모두 개선.

### P2 — `_per_file_movers` 의 비교 기준이 "iter_0 vs iter_last" 라 baseline 대비 movement 가 누락

`scripts/analyze_run.py:386-410`:

```python
first = iters[0].per_file
last = iters[-1].per_file
```

기대 동작 (PHASE2-PLAN §3.3 C): "어떤 wav 가 가장 큰 개선, 어떤 wav 가 정체".
정확한 비교 baseline 은 사람이 정한 baseline (faster-whisper / Phase 1 baseline)
또는 잡 *시작 직전* 의 commit. iter_0 은 이미 첫 번째 가설로 baseline 과 다른
지점.

관찰 동작: 잡이 regression 으로 끝나면 (iter_last 가 iter_0 보다 더 나쁨)
movers 가 마이너스 방향으로 정렬 — "정체 파일" 이 실제로는 "처음엔 잘했다가
나빠진 파일" 일 수 있고, "가장 큰 개선" 이 실은 첫 시도부터 갖고 있던 자산.

수정 방향: `iters[0]` 대신 (a) `baseline/target_cer.json` 의 `per_file` 을
baseline 으로 쓰거나, (b) "잡 마지막 *채택* iter" 와 "잡 첫 *채택* iter" 를
비교. 둘 중 정본 정의 후 반영.

명세 위반은 아님 (어떤 baseline 을 쓸지 §3.3 C 가 명시 안 함) — 그래서 P2.

### P2 — `evaluate_holdout._per_file_table` 가 index 기반 사이드 바이 사이드라서 오인 위험

`scripts/evaluate_holdout.py:123-145` 가 eval 11 파일과 holdout 13 파일을
*같은 행* 에 (i=0, 1, 2, ...) 묶어 출력. 표 위에 "별개 파일 집합" 디스클레이머가
한 줄 있지만, 표 자체는 "왼쪽 행 ↔ 오른쪽 행" 인상 강함.

기대 동작 (PHASE2-PLAN §4.1): "per-file CER 비교" — 의도는 *분포 비교* 인데
표 모양이 "쌍" 으로 읽힌다.

관찰 동작: 분석자가 "iter 1 의 file_a 가 holdout 의 file_x 와 페어" 라고 오해할
여지. eval/holdout 모두 wav 경로 sort 순이라 어떤 패턴으로 자동 align 되는
것처럼 보임.

수정 방향: 두 컬럼을 **별개 테이블 두 개** 로 분리하거나, 행 정렬 자체를
"cer 내림차순" 으로 명시 (분포 비교 의도 강조). 또는 corpus 통계 한 줄로 줄여
오인 여지 제거.

### P2 — `enrich_with_git` 의 sidecar 형식이 정본화 안 됨

`scripts/analyze_run.py:202-211` 가 `runs/<hyp_id>/commit.txt` 를 읽고
`first, *rest = data.splitlines()` 로 첫 줄 = sha, 두 번째 줄 = subject 로 가정.

그러나 이 sidecar 형식은 어떤 정본 문서 (PHASE2-PLAN, PHASE3-PLAN,
STT-PIPELINE-SPEC) 에도 명시돼 있지 않다. 만약 Phase 3 autoresearch 가
`echo "$(git rev-parse HEAD) $(git log -1 --pretty=%s)" > commit.txt` 형태로
한 줄에 쓰면 (자연스러운 한 줄 dump), `first` 는 "sha subject" 전체가 sha 로
오인되고 `subject` 는 None.

기대 동작: sidecar 형식이 명시 사양에 박혀 있고 양쪽이 따른다.

관찰 동작: 사양 누락. analyze_run 의 docstring 만이 사실상 의미를 정한다.

수정 방향: PHASE2-PLAN §3 또는 PHASE3-PLAN 의 산출물 명세에 "`commit.txt`: 1행
sha + 2행 subject (옵션)" 를 한 줄 박아두기. 또는 분명한 구조 (JSON sidecar
`commit.json: {sha, subject, ts}`) 로 옮겨 sidecar 형식 추측을 제거.

### P2 — `test_dry_run_with_lock_succeeds` 가 환경 의존적 (rc=0 또는 rc=3 둘 다 허용)

`tests/test_evaluate_holdout_smoke.py:49-65`:

```python
assert result.returncode in (0, 3), (result.stdout, result.stderr)
```

코멘트가 "rc=3 may happen if the project's data/raw doesn't exist on this machine"
라고 인정.

기대 동작: dry-run smoke 는 lock 있고 데이터 root 도 *없어도* 0 으로 통과하거나,
일관되게 한 가지 코드 경로만 검증. 두 케이스를 같이 허용하면 어떤 머신에서는
실제 dry-run 본문 (line 308-313) 이, 다른 머신에서는 data root 거부 (line 295-297)
가 검증되는 셈.

관찰 동작: CI / 회사컴 / 개인컴 결과가 의도치 않게 다른 코드 경로를 통과.
회귀 검출력이 약함.

수정 방향: 테스트에서 `ASR_RAW_DATA_ROOT` 를 `tmp_path / "data_raw"` 로 명시 지정
+ 안에 `wav/AIG_녹취반출_20250813`, `label/AIG_녹취반출_20250813` 빈
디렉토리 생성. 그러면 rc=0 한 가지로 결정론적.

`test_default_refuses_without_unseal` 가 이미 그 패턴을 쓰고 있어 (line 89-91)
일관 가능.

### P2 — `_recovery_and_streak` 의 dead code `n_after_rollback += 0`

`scripts/analyze_run.py:621`:

```python
else:
    n_after_rollback += 0  # noop; counted on next accept
```

`x += 0` 은 무의미. 주석은 "next accept 에서 카운트" 라고 적었지만 그 분기에는
분모 증가가 없다 (P1 finding 참조). 무의미한 noop 이라기보다는 누락된 로직의
흔적.

수정 방향: P1 의 recovery rate 교정과 함께 제거.

### P2 — `_REASONING_RULES` 의 `length` 부호가 단일 방향이라 over-emission 가설에는 거짓 misalign

`scripts/analyze_run.py:57-58`:

```python
"length": ("length_ratio.mean", +1),
"길이": ("length_ratio.mean", +1),
```

코멘트 "length up = closer to ref when stub under-emits" 는 현재 stub 의 length
ratio mean ~0.6 이라는 *현시점* 컨텍스트에 묶인 가정. Phase 3 중 어떤 가설이
length ratio mean = 1.2 (over-emission 환각) 를 1.0 으로 끌어내리려고
"길이 조정" commit 을 쓰면, 메트릭은 감소해야 일치인데 코드는 증가를 기대 →
거짓 mismatch.

기대 동작 (PHASE2-PLAN §3.3 H): "키워드-메트릭 방향 일치" 의 자동 보조 — 사람이
들여다볼 후보 *목록* 을 만드는 게 목적.

관찰 동작: 거짓 mismatch 가 후보 목록에 추가 → 사람 부담 증가. 정합/부정합 카운트
숫자가 어긋남.

수정 방향: length 메트릭은 (목표 1.0 에서 멀어지면 나쁨, 가까워지면 좋음)
신호이므로 `(prev_v - 1.0)` 의 절댓값이 줄었는지로 판정. 또는 length 키워드는
자동 alignment 검사에서 제외하고 사람 칸으로만 남기기.

이 항목은 H 축이 "자동 보조 + 사람 판정" 둘 다 명시한 영역이므로 자동 부분의
잘못된 신호는 사람 판정으로 흡수됨 — P0/P1 아님.

### P2 — `analyze_run` 의 `datetime.utcnow()` deprecation

`scripts/analyze_run.py:713`:

```python
"generated_at": datetime.utcnow().isoformat() + "Z",
```

`datetime.utcnow()` 는 Python 3.12 부터 `DeprecationWarning`. 같은 코드베이스
다른 곳 (`evaluate_holdout.py`, `judge/evaluate.py`) 은 `datetime.now(UTC)` 로
일관 — analyze_run 만 어긋남.

수정 방향: `datetime.now(UTC).isoformat().replace("+00:00", "Z")` 또는 import 한
`UTC` 사용.

### P2 — `discover_iterations` 의 정렬 키 `produced_at` 충돌 시 동작 미정의

`scripts/analyze_run.py:164` `out.sort(key=lambda r: r.produced_at)`.

동일 `produced_at` (같은 초 안에 두 iter 끝남 — autoresearch 가 빠르거나 stub
이 빠를 때 가능) 시 Python sort 가 stable 하므로 디렉토리 iter 순서 (= sorted
`runs/` 순서, line 144) 를 그대로 유지. 즉 hyp_id 알파벳 순. 명세된 순서가
아니라 implicit. 잡 중 실제 commit 순서와 다를 수 있다.

수정 방향: 2 차 정렬 키로 `(produced_at, commit_ts or produced_at, hyp_id)` 처럼
명시. 또는 sidecar `commit.txt` 또는 `_telemetry/` 의 emission ts 를 1 차 키로
승격.

명세 위반 아님 — `discover_iterations` 의 출력 순서가 무엇이어야 하는지 정본
문서가 정의하지 않음.

### P2 — `_per_file_dispersion` IQR 계산이 단순화된 근사 (작은 N 에서 표준 IQR 과 다름)

`scripts/analyze_run.py:498-500`:

```python
q1 = xs[len(xs) // 4]
q3 = xs[(3 * len(xs)) // 4]
```

N=11 (현재 eval batch) → q1 = xs[2], q3 = xs[8]. 이는 nearest-rank 방식의
근사이며, type-7 (numpy default, Excel) IQR 과 약 1 칸 차이. 분석 목적
("한두 파일이 점수를 끌고 갔는가") 의 진단에는 충분.

기대 동작: PHASE2-PLAN §3.3 E "per-file CER 의 표준편차 / IQR" — 정의 안 박힘.

관찰 동작: nearest-rank 단순 근사. 표기에 "근사 quartile" 명시 권장.

수정 방향: `statistics.quantiles(xs, n=4, method="exclusive")` 사용 또는 함수
docstring 에 사용 방법 적시. statistics 표준 라이브러리 (Python ≥3.8) 이라 추가
의존성 없음.

### P2 — `evaluate_holdout._is_sealed` 가 dead code

`scripts/evaluate_holdout.py:67-81` `_is_sealed()` 함수가 정의돼 있으나 어디서도
호출 안 됨. 또한 함수 내부 로직이 모호함 — `try: next(target.iterdir())` 후 같은
`target.iterdir()` 를 재차 호출하고 PermissionError 만 검사. 빈 디렉토리와
sealed 디렉토리를 구분하려는 의도는 보이나, 구현이 어색.

수정 방향: 호출처가 없다면 삭제. 진짜 필요하다면 `os.access(target, os.R_OK)`
또는 `stat().st_mode & 0o400` 으로 단순화 후 main() 에서 unseal 전 가드.

### P2 — `enrich_with_git` 가 git 부재 / runs 가 비-git 디렉토리일 때 silent fallback

`_git` 은 `FileNotFoundError` / `CalledProcessError` 를 빈 문자열로 swallow.
`commits = []` 가 되어 모든 iter 의 commit_subject = None → `_categorize` →
"unclassified" 일색.

기대 동작: 운영 환경 가정 위반 시 사용자에게 알려야 — Phase 3 잡은 git 위에서
돌도록 PHASE3-PLAN 이 가정.

관찰 동작: 경고 하나 없이 D 축이 무력화. analyze_run 결과만 보면 "정말로 모든
가설이 unclassified 였다" 와 "git 이 없어서 분류 못 했다" 가 구분 안 됨.

수정 방향: `_git` 가 실패하면 한 번 `log.warning("git unavailable — category /
sidecar 분류 skip")` 출력. `enrich_with_git` 진입에 git 가용성 1 회 체크.

### P2 — `evaluate_batch` 호출의 `transcribe_spec` 이 하드코딩

`scripts/evaluate_holdout.py:336-340`:

```python
holdout_score = evaluate_batch(
    batch=_HOLDOUT_BATCH,
    transcribe_spec="workspace.transcribe:transcribe",
    out_path=holdout_run_dir / "score_report.json",
)
```

표면 명시는 workspace/transcribe.py 한 파일 (AGENTS.md §1 표) 이라 spec 위반은
아님. 다만 Phase 3 종료 시점에 평가자 진입점이 이 spec 과 일치한다는 보장이
script 안에 박혀 있음 — workspace 측에서 함수 이름을 바꾸면 holdout 평가만 깨짐.

수정 방향: `--transcribe-spec` CLI 인자로 빼되 default = 현재 값. 또는
`baseline/target_cer.json` 같은 정본에 transcribe_spec 을 등록하고 거기서 읽음.

명세 위반 아님 — P2 robustness.

### P2 — `render_report` 의 substitution 이 값 자체에 `{{key}}` 패턴 있으면 재치환

`scripts/analyze_run.py:784-786`:

```python
out = template
for key, val in substitutions.items():
    out = out.replace("{{" + key + "}}", val)
```

dict iteration 순서가 Python 3.7+ 부터 insertion order. `val` 안에 다른 substitution
의 패턴 (`{{job_id}}` 등) 이 들어가면 다음 라운드에서 또 치환됨. 현재 모든 val
은 safe 한 출처 (analysis output, 숫자, table) 라 실제 사고 가능성 낮음. 다만
사람이 `--job-id "{{leak}}"` 처럼 인자로 패턴 비슷한 문자열을 넘기면 약한
template injection 가능.

수정 방향: 정규식 한 번에 전체 치환 (`re.sub(r"\{\{(\w+)\}\}", lambda m:
substitutions.get(m.group(1), m.group(0)), template)`) 또는 `string.Template`
사용. 운영 관점에서 실손해는 작음.

### P2 — `_chmod_recursive` 가 `chmod -R` 을 호출 — 심볼릭 링크 대상까지 권한 전파 가능

`scripts/evaluate_holdout.py:56-64` `_chmod_recursive` 는 `chmod -R 755`
(또는 000) 을 shell 로 호출. GNU `chmod -R` 의 default 는 **심볼릭 링크를 따라가지
않음** (POSIX 표준). 본 머신/회사컴 모두 holdout 이 실 디렉토리 (복사된 실파일)
인 상태로 확인되므로 현 시점 실손해 없음.

기대 동작: holdout 디렉토리가 어떤 형태든 (실 디렉토리, bind mount, symlink-to-real)
unseal/seal 가 그 디렉토리에만 적용.

관찰 동작: 현재는 안전. 다만 미래에 `data/raw/wav/AIG_녹취반출_20250813` 이 외부
스토리지로의 symlink 가 되면 → `-R` 가 따라가지 않으므로 symlink 자체의 mode 만
바뀌어 unseal 효과가 없어짐 (반대 위험). 또한 디렉토리 안에 무관한 symlink 가
들어가면 그 link 들의 *대상* 은 그대로지만 link 자체 mode 가 바뀜.

수정 방향: 명세에 "holdout 은 실 디렉토리만, symlink 거부" 박고 unseal 직전
`if target.is_symlink(): raise` 가드. PHASE2-PLAN §4 또는 PHASE3-PLAN §6.

명세에 symlink 정책이 없으므로 P0 격상 X — 정본화 권고.

---

## Notes

- 템플릿 변수와 substitution dict 의 1:1 일치 확인: 53 개 `{{var}}` 모두
  `render_report` 에 대응 (smoke test `assert leftovers == []` 가 이를 enforce).
  P0 항목 없음.
- PHASE2-PLAN §7 DoD 6 항목 모두 형식적 충족: analyze smoke / evaluate smoke /
  8 축 변수 정의 / pytest 통과 (smoke 코드 확인 기준) / 사람 칸 보존 / commit.
- PHASE2-PLAN §1 표의 "자동/수동" 분리는 잘 지켜짐. 자동 칸이 임계 단언으로
  사람 판단을 침범하는 흔적은 없음. `_concentration_warning` 의 ⚠ 출력과
  `_threshold_warnings` 의 "임계 재조정 후보" 표현은 §3.3 B / §3.3 D 의
  자동 산출 정의 안에서 허용된 신호 — 의사결정이 아니라 *후보 제시*.
- 결정 임계 정합성 (`Δcer ≥ 2σ` ↔ `is_provisional` 시 absolute 0.01):
  `analyze_run._delta_threshold` 와 `evaluate_holdout._write_holdout_report` 두 곳
  모두 `noise_floor.is_provisional` 와 `sigma > 0` 두 조건을 같은 방식으로
  체크 — 정합.
- `_top3_share` 의 첫 iter 제외 (delta_from_best = None) 는 의도된 동작이지만,
  잡 첫 가설이 baseline 대비 큰 개선을 만들면 그 기여가 G 축 attribution 에서
  누락. 정본에 "delta 정의 = vs running_best" 라고 명시돼 있지 않다면 P3 정도의
  documentation 항목.
- `test_analyze_smoke.py` 의 trajectory 단언 (`iter_03 in accepted`,
  `iter_04 not in accepted`) 은 noise_floor sigma=0.02 → 2σ=0.04 임계와 정확히
  맞아 떨어지게 설계됨. 회귀 검출력 OK.
- `evaluate_holdout` 의 `_HOLDOUT_BATCH` / `_EVAL_BATCH` 상수는 하드코딩이지만
  STT-PIPELINE-SPEC / PHASE3-PLAN 이 두 batch 를 정본화하므로 spec 위반 아님.
