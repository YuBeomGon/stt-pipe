# 의미 있었던 시도 5선 — harness · code auto-gen 관점 회고

> 작성: 2026-06-12 / 대상: phase3_002~030 + simple_001~006 (총 20여 run, ~400 iteration)
>
> self-evolve 하네스로 STT 파이프라인(`workspace/transcribe.py`)을 자동 진화시킨
> 전체 히스토리에서, **하네스 설계**와 **코드 자동생성(auto-gen) 동작 로직** 관점에서
> 의미가 컸던 시도 5개를 추려 정리한다. 각 시도는 *가설 → 방법 → 결과 → 배운 것*
> 순서로 기록한다.

---

## 0. 배경: 하네스 한 줄 요약과 세대 구분

하네스 루프의 골격은 모든 세대에서 동일하다:

```
parent 선택 → 프롬프트 조립(profile + mode directive + parent 코드 + 피드백)
→ claude -p 후보 세션 (transcribe.py 한 곳만 수정)
→ 게이트(format/scope/static guard) → judge 평가(corpus_cer)
→ keep/reject 판정 → 상태 기록 → 반복
```

세대별 변천 (상세: [`docs/HARNESS-EVOLUTION.md`](../HARNESS-EVOLUTION.md)):

| Gen | 시기 | 핵심 메커니즘 |
|---|---|---|
| Gen0~2 | phase1~3_002 | 평가 인프라·context 격리·YAML format gate |
| Gen3~4 | phase3_003~005 | findings ledger·cold-restart·error profile 주입 |
| Gen5 | phase3_006~007 | portfolio 뱅크 + 6-mode 스케줄러 + evaluated budget |
| Gen6 | phase3_008~014 | family 계보·near-best pool·plateau burst·DIVERGE 지시 |
| Gen7 | phase3_015~030 | metadata off-git·champion ref 분리·lineage set·gated promotion |
| Gen8 | simple_001+ | 구조 제거: never-prune archive + LLM parent 선택 + coin-flip 모드 |

기준 수치: baseline(faster-whisper, 0715 eval) `corpus_cer = 0.4126`(stub 기준) /
앵커 `0.1714`, 목표 `0.10`.

---

## 1. phase3_013_iter_022 — `detect_language()` per-window override (전체 최고 기록)

**CER 0.153887 — 전 run 통틀어 최저.** (2026-06-04, 26 iter 중 13 keep)

### 가설
주 오류축이 substitution(58.9%)인데, 그 상당 부분이 code-switch 된 영어
도메인 용어(보험·상품명)가 한글로 음차되는 문제. Whisper encoder 는 window 별
language posterior 를 이미 계산하므로(`detect_language()` — 그때까지 아무도
호출한 적 없던 backend 의 세 번째 메서드), 고정 `<|ko|>` 토큰 대신
**window 단위로 언어 토큰을 조건부 교체**하면 음차 substitution 을 없앨 수 있다.

### 방법 (candidate 가 생성한 패치 핵심)
```python
detected = model.detect_language(features)[0]   # [(lang_token, prob), ...]
det_token, det_prob = detected[0]
_LANG_OVERRIDE_PROB = 0.7
if det_token != _LANGUAGE_TOKEN and det_prob >= _LANG_OVERRIDE_PROB:
    lang_id = tokenizer.convert_tokens_to_ids(det_token)   # 확신할 때만 교체
else:
    lang_id = ko_id
base_sot = [sot_head, lang_id, task_id]                    # per-window SOT
```
계보 누적분 위에 얹힘: timestamp-seek(iter_002) → RMS silence cut(iter_006)
→ compression/logprob gate(iter_009) → context carry(iter_010) → beam 5 +
patience 2.0(iter_011~012) → notimestamps clean decode(iter_013) → align tail
trim(iter_016). 즉 **13단계 keep 의 계단식 스택** 맨 위의 한 수.

### 결과
- eval 0715: **0.153887** (iter_016 의 0.154828 에서 한 단계 더)
- 비용: window 당 encoder pass 1회 추가, 224.7s / budget 719.9s — 여유
- 비교 실험 iter_023(애매 구간 [0.4, 0.7) dual-decode)은 0.154541 —
  **"확신할 때 한 번만 교체"가 "두 번 디코드 후 비교"를 이김**

### harness / auto-gen 관점 의미
- **surface 탐사가 점수를 만든다는 증거.** candidate profile 의 "frozen
  backend 소스가 곧 지도다 — 안 써본 반환 채널을 찾아라"라는 지시가
  실제로 미사용 API(`detect_language`) 발굴 → 최고 기록으로 직결.
- **EXPLORE 지시문의 성공 사례**: "knob 하나 돌리지 말고 안 써본 backend
  채널을 써라"(DIVERGE directive)를 candidate 가 문자 그대로 수행.
- 단, 이 한 수가 가능했던 건 12단계의 refine/repair 누적 위였다는 점이
  phase3_014 의 발견(§3)과 연결된다.

### 아티팩트
- `runs/_archive/phase3_013_iter_022/` (candidate.diff, score_report.json, prompt.md 70KB)
- 복원 코드: `temp/cer15_transcribes/phase3_013/phase3_013_iter_022/transcribe.py`
- 카탈로그: [`phase3_013_014_algorithm_catalog.md`](phase3_013_014_algorithm_catalog.md) (재현 우선순위 #1),
  [`cer15_method_and_parameter_catalog.md`](cer15_method_and_parameter_catalog.md) §2, §4.1

---

## 2. simple_003 — acoustic-VAD 발견과 holdout 과적합 진단 (요청 포함 run)

**eval CER 0.175731 (iter_0006) → holdout CER 0.216616 (Δ +0.041, 과적합 검출)** (2026-06-06, 8 iter)

### 가설
(iter_0006) decoder 의 timestamp 토큰은 무음·패딩 window 에서 불안정하다.
세그멘테이션을 decoder 출력에서 **분리**해, 원파형의 short-time RMS 엔벨로프로
무음 구간을 찾아 거기서 자르면(acoustic-VAD) 발화 중간 절단이 사라진다.

### 방법
```
_FRAME_SECONDS=0.03, _MIN_SILENCE_SECONDS=0.25,
_SILENCE_RATIO=0.15 (90-percentile RMS 대비), _MIN_SEGMENT_SECONDS=2.0
```
RMS 프레임 → 무음 run 의 중앙을 cut point → 최대 30s 세그먼트 →
beam 5 + `<|notimestamps|>` clean decode. 8 iter 궤적:
`0.4126 → (iter_0002 timestamp-seek) 0.2241 → (iter_0006 acoustic-VAD) 0.1757`.

### 결과 — 그리고 반전
운영자가 수동으로 holdout(0813, 학습 cutoff 이후 수집)을 돌리자:
- eval 0715: 0.1757 / **holdout 0813: 0.2166** (Δ +0.041)
- guard 신호: `repeated_text_rate` eval 0% vs holdout **15.4%** —
  eval 셋 경계에 맞게 cut 이 정렬된 **데이터 적합**이었음이 드러남

### harness / auto-gen 관점 의미
- **단일 eval 배치 hill-climbing 의 구조적 위험을 처음 정량화한 run.**
  하네스는 보통 "코드를 바꾸고 데이터를 고정"하는데, simple_003 은 반대로
  "모델을 얼리고 데이터를 스트레스 테스트"해서 15%대 후보들이 알고리즘적
  강건함이 아니라 데이터 적합일 수 있음을 보였다.
- 이 진단이 **holdout 운영 정책**(자동 holdout 제거, 운영자 수동 게이트 —
  commit `0a0e349`)과 guard 메트릭(반복률·길이비) 배치별 추적의 근거가 됐다.
- auto-gen 관점: acoustic-VAD 자체는 Gen8 simple 프롬프트(모드 2개뿐)에서도
  재발견됨 — **복잡한 스케줄러 없이도 핵심 발견은 나온다**는 Gen8 베팅의
  초기 근거.

### 아티팩트
- `runs/simple_003/0006/` (candidate.diff, transcribe.py, score_report.json)
- `runs/_summary/simple_003_log.jsonl`
- 리포트: [`simple_003_HOLDOUT_2026-06-06.md`](simple_003_HOLDOUT_2026-06-06.md)

---

## 3. phase3_014 — "explore 단독으론 champion 을 못 이긴다" (하네스 핵심 발견)

**90 iter, 최고 기여 iter_046 glossary margin 0.158540** (Gen6 하네스 검증 run)

### 가설
(하네스 가설 F2) 다양성 부족이 정체 원인이므로, DIVERGE 지시 + diversity_stall
오버라이드로 explore 빈도를 올리면 champion(0.1539)을 넘는 새 메커니즘이 나온다.

### 방법
Gen6 풀 스택: 6-mode 스케줄러(explore/refine/repair/synthesize/combine/ablate),
family 계보 상속, near-best pool(champion CER 1.20× 이내 보존), plateau burst,
diversity_stall 시 explore 강제. 90 iteration 장기 run.

### 결과
- **explore 슬롯에서 champion 갱신 0회.** 개선은 전부 refine/repair 에서 나옴
  (대표: iter_046 glossary logprob margin 0.1 → 0.158540, 도메인 용어
  false-negative 해결).
- diversity_stall 이 과발동해 오히려 exploit 을 방해.
- 결론: **다양성은 "지시"로 만들어지지 않는다.** DIVERGE 는 1-shot 발산을
  만들 뿐, 발산된 씨앗이 자라려면 refine 사이클을 이어 받아야 하는데
  당시 구조(reject 시 champion 으로 즉시 rollback — single-HEAD 문제)가
  그걸 불가능하게 했다.

### harness / auto-gen 관점 의미
- F2(다양성 가설)를 **약화**시키고, 진짜 병목이 git HEAD 하나가 4역할
  (작업트리/rollback 기준/계보 기원/diff 기준)을 겸하는 **구조 문제(C1/C2)**
  임을 지목 → Gen7 refactor-harness(champion ref 분리, lineage set,
  gated promotion)의 직접적 설계 근거.
- auto-gen 관점: "프롬프트 지시(소프트웨어)로 풀 수 없는 문제는 하네스
  구조(하드웨어)에 있다"는, 이 프로젝트에서 가장 비싼 교훈을 산 run.

### 아티팩트
- `runs/_archive/phase3_014_iter_*/` (90 iter)
- 분석: [`docs/HARNESS-EVOLUTION.md`](../HARNESS-EVOLUTION.md) §6 (교훈),
  [`phase3_013_014_algorithm_catalog.md`](phase3_013_014_algorithm_catalog.md)
- 메모리/회고: single-HEAD C1/C2 진단

---

## 4. phase3_016_iter_010 — axis-aware selector (참신한 선택 로직)

**CER 0.160440** (Gen7 초기 smoke run 중 발굴)

### 가설
대부분의 시도는 더 나은 후보를 **만드는** 쪽(beam 확대, MBR, ensemble)인데,
이미 있는 N-best 풀에서 **고르는 함수**가 오류 유형을 모른다는 게 손실이다.
후보의 실패 양상(failure axis)별로 tie-break 기준을 바꾸면 추가 디코드
비용 없이 substitution/반복 붕괴를 동시에 줄일 수 있다.

### 방법
N-best 후보를 두 부류로 진단 후 선택 기준 분리:
- **반복 붕괴 후보** (divergent/repeating) → compression ratio 우선
  (빠른 루프 검출기 역할)
- **정상 후보** → avg logprob 우선 (신뢰도)

같은 후보 풀, 같은 디코드 비용. 선택 함수만 교체.

### 결과
- 0.160440 — 당시 16%대 진입 상위권. beam 8 확장(0.1628)보다 싸고 좋음.

### harness / auto-gen 관점 의미
- **"proposal 메커니즘"이 아니라 "selector 함수"가 진화 축이 될 수 있다**는
  걸 보인 유일한 사례. 오류 진단(발산 검출)이 선택 로직으로 피드백되는
  구조 — auto-gen 이 탐색해야 할 공간이 디코딩 파라미터 평면보다 넓다는 증거.
- 이후 phase3_030 의 MBR/medoid, dual front-end 점수 중재(iter_076~080) 등
  "selector 계열" 시도들의 출발점.

### 아티팩트
- `runs/phase3_016_iter_010/score_report.json`
- 인벤토리: [`all_experiment_idea_inventory.md`](all_experiment_idea_inventory.md) (16% 초반 표, selector 축)

---

## 5. phase3_030 — 다양성 "생성 vs 육성" 진단 (Gen7 본 run, 107 iter)

**from-scratch 0.4126 → 0.162836 (iter_097) — 목표 0.154 미달, 그러나 가장 많은 것을 가르친 run** (2026-06 초)

### 가설
Gen7 구조(champion ref 분리 + lineage set + near-best 보존 + gated promotion)면
phase3_014 의 구조 병목이 풀렸으니, from-scratch 에서 champion 기록(0.1539)을
재현·갱신하고 다가족(multi-family) 다양성이 유지될 것이다.

### 방법
107 iteration 풀 run. 세그멘테이션(RMS trough iter_094~098), 오디오 프런트엔드
(pre-emphasis·loudness norm·dual front-end 중재 iter_076~080), sampling MBR
(iter_051~056), recurrence bank/term canonicalization(iter_102~103),
ROVER/ensemble(iter_082~091) 등 광범위 축 탐색.

### 결과
- 최고 iter_097: RMS trough 세그먼트 + beam 8 재시도 = **0.162836**
  (timestamp-seek 위에 VAD 정밀화를 얹은 2단 계층 — 세그멘테이션 축과
  탐색 축이 직교함을 입증)
- 그러나 0.154 미달. 사후 분석:
  - 한 계보가 **74% 과점** (78/105 iter) — family 상속 규칙(refine/repair/
    combine 이 parent family 승계)이 계보 라벨을 집중시킴
  - 신규 family 는 set budget 안에서 **조기 사망**
  - ensemble/ROVER 는 구성원이 동질이라 **무력** (470s+ 비용만 소모)
- 참신 시도로 recurrence bank(iter_102): 확정 출력에서 동적으로 용어를
  수확해 후속 window 의 prior 로 쓰는 "lexical memory" — 정적 glossary 와
  rolling context 의 중간 지대 개척 (0.168168).

### harness / auto-gen 관점 의미
- **"다양성은 생성되지만 육성되지 않는다"** — 구조(Gen7)는 작동했고
  rollback 정밀도 문제는 풀렸지만, 다양성이 점수로 전환되려면 생성 이후의
  *배양 정책*(신생 가족 보호, 이질성 기반 ensemble 구성)이 따로 필요.
- 구조를 더 쌓는 방향(스케줄러·portfolio·set)의 **수확체감을 확정** —
  Gen8 simple-evolve(구조 제거, LLM 에게 parent 선택과 판단을 위임,
  never-prune archive)로 전환한 직접 근거. 결정 로그 #14.
- auto-gen 관점: 107 iter 의 keep/reject 전 기록(`runs/_summary/HISTORY.md`,
  decisions.jsonl)이 남아, "LLM 후보가 어떤 피드백에서 어떤 가설을 내는가"의
  최대 규모 데이터셋이 됐다.

### 아티팩트
- `runs/phase3_030_iter_*/`, `runs/_summary/HISTORY.md` (1168줄 전체 결정 로그)
- `runs/_summary/phase3_030_candidate_meta.jsonl`, `phase3_030_decisions.jsonl`
- 회고: [`docs/HARNESS-EVOLUTION.md`](../HARNESS-EVOLUTION.md) §6

---

## 6. 종합 — 다섯 시도가 그리는 궤적

| # | run | CER | 발견의 종류 |
|---|---|---|---|
| 1 | phase3_013_iter_022 | **0.1539** | 파이프라인: 미사용 backend API 발굴이 최고 기록 |
| 2 | simple_003 (+holdout) | 0.1757 / 0.2166 | 평가 방법론: eval 적합 검출, holdout 정책 수립 |
| 3 | phase3_014 | (0.1585) | 하네스 구조: 프롬프트 지시의 한계, C1/C2 진단 |
| 4 | phase3_016_iter_010 | 0.1604 | 탐색 공간: selector 함수도 진화 축이다 |
| 5 | phase3_030 | 0.1628 | 메타 전략: 다양성 생성≠육성, 구조 수확체감 → Gen8 |

**code auto-gen 루프의 동작 원리로 요약하면:**

1. **surface 가 지도다** — candidate 에게 frozen backend 전체 소스를 인라인으로
   주고 "안 써본 채널을 찾아라"고 한 것이 detect_language(#1), align 계열,
   no_speech_prob 계열 발견의 공통 기원.
2. **점수는 계단으로 온다** — 최고 기록은 단발 천재수가 아니라 13단계 keep
   스택의 마지막 한 수(#1). 따라서 발산(explore)보다 **발산의 후속 배양**
   (refine/repair 승계 구조)이 병목(#3, #5).
3. **평가가 곧 진화 압력이다** — eval 셋 하나만 보면 그 셋에 적합한다(#2).
   guard 메트릭(반복률·길이비·환각률)과 holdout 게이트가 없으면 CER 하강이
   진보가 아닐 수 있다.
4. **프롬프트로 안 풀리면 구조 문제다** — DIVERGE 지시 과발동(#3)과
   계보 74% 과점(#5)은 둘 다 지시문이 아니라 git/상태 구조의 산물이었다.
5. **구조의 끝은 단순화였다** — Gen5~7 의 정교한 스케줄러·portfolio·lineage 는
   각자 문제를 풀었지만 수확체감에 도달했고, 현재(Gen8 simple-evolve)는
   never-prune archive + LLM parent 선택 + EXPLORE/EXPLOIT coin flip 으로
   판단을 LLM 컨텍스트에 위임하는 베팅을 검증 중이다.

### 참고 문서
- 하네스 알고리즘 정본: [`docs/HARNESS-ALGORITHM.md`](../HARNESS-ALGORITHM.md)
- 세대별 변천사: [`docs/HARNESS-EVOLUTION.md`](../HARNESS-EVOLUTION.md)
- 전체 아이디어 인벤토리: [`all_experiment_idea_inventory.md`](all_experiment_idea_inventory.md)
- CER 15% 재현 카탈로그: [`cer15_method_and_parameter_catalog.md`](cer15_method_and_parameter_catalog.md)
- candidate 프롬프트: `harness/prompts/candidate.md`, `harness/prompts/candidate_simple.md`
