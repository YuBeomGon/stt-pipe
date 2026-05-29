# Proposal — Candidate Prompt Diversification (Discovery-first)

- 상태: **Filled (post-phase3_002)** — 선행 skeleton 을 phase3_002 결과로 채우고
  *발견형(discovery-first)* 방향으로 재정렬. 원 처방형 메커니즘은 §4 로 보존.
- 작성: 2026-05-29 (skeleton) / 갱신: 2026-05-29 (phase3_002 후 본문)
- 선행: [`2026-05-29-agent-design.md`](2026-05-29-agent-design.md) (A' candidate runtime)
- 영향: `harness/prompts/candidate.md`, `harness/runner.py` (prompt 빌더),
  `harness/state.py`
- 트리거: 선행 RFC §2.2 의 deferred "cold-restart wildcard (M3)" 슬롯,
  RFC §4 판정 표의 phase3_002 lane entropy / streak 결과

---

## 0. phase3_002 결과 (트리거 데이터)

본 proposal 의 도입 여부·범위는 phase3_002 (25 iter) 실측에 따른다 (§4 판정표).

| 항목 | 값 |
|---|---|
| 시작 cer (iter1) | 0.411 |
| 최종 best cer (iter10, 마지막 keep) | **0.169** |
| 잡 중 최저 raw cer (iter18) | 0.161 (Δ < 게이트 → reject) |
| keep iter | 1 · 2 · 6 · 8 · 10 (전부 전반 10 iter) |
| best 미갱신 streak | **15** (iter11~25 연속 reject) |
| target / 앵커 | 0.10 / 0.4685 |
| lane entropy / fingerprint Jaccard | **n/a — 측정 불가** (아래 ⚠️) |

### Holdout (0813) — overfit 신호

| 항목 | 값 |
|---|---|
| eval (0715) corpus_cer | 0.1548 |
| holdout (0813) corpus_cer | 0.1936 |
| Δ (holdout − eval) | **+0.0388 → Overfit 의심 YES** (`|Δ| > 게이트`) |
| repeated_text_rate (eval → holdout) | 0.091 → **0.250** (+0.159) |
| hallucination_hit_rate (eval → holdout) | 0.000 → **0.0625** |

→ 미지 오디오에서 **반복/환각 급증**. 현 파이프라인(silence-snap 청킹 + fallback
부재 + no_speech 게이팅 부재)이 다른 오디오 특성에 취약. fallback / no_speech /
반복억제는 candidate 가 한 번도 안 건드린 표면 → **발견형(§2.2) 방향이 정확히 이
overfit 갭을 겨냥**.

### ⚠️ REPORT 비어있음 — 다양성 데이터 부재

`analyze_run.py` 는 "분석 대상 runs: **0 개**" 로 산출됐다. `runs/<hyp_id>/`
산출물이 gitignore 라 디스크에 없고 **`candidate_meta.json` (lane/fingerprint
정본) 도 부재** → REPORT D축의 lane entropy / Jaccard / format-reject 비율이 전부
`n/a`. 즉 §5 판정의 *entropy 근거 데이터가 존재하지 않는다*. 판정은 HISTORY 에서
복원한 **streak=15 단독** 으로 내린다. (이 자체가 분석 인프라 갭 — §7 작업 항목.)

**판정 신호**: streak = 15 ≫ 5 → §5 판정표상 `streak ≥ 5 → 전면 도입` 구간.
다만 "전면"의 *내용* 을 원 skeleton 의 처방형이 아니라 **발견형 우선** 으로 채운다
(§2 근거).

추가 관찰 — iter12=0.166, iter18=0.161 은 raw 로 best 를 깼으나 Δ가 게이트(0.01)
미달로 기각. 같은 방향 sub-threshold 이득의 누적 신호가 매번 폐기됨 (§3.4 / §6 참조).

---

## 1. 배경

선행 RFC §1.2 에서 phase3_001 의 "chunking 83%" 단조성을 *3 층 가설* 로 분리:

- **L1**: candidate 사고 부재 (역할·접근법 안내 없음)
- **L2**: 컨텍스트 오염 (HISTORY tail anchoring 등)
- **L3**: 다양성 강제 없음 (같은 fingerprint 반복 허용)

A' (proposal-1) 는 L1 단독 해결 — profile + YAML gate + lane round-robin
권고. L3 의 *iter 단위 다양성* 은 부분적으로 다룸 (fingerprint 기록, 자기-검증
체크리스트).

**누락된 것**: *메타 단위 다양성* — "지금까지 한 접근 전부 무효, 원점에서 재고"
류. L3 의 *깊은 부분* + L2 의 일부 (HISTORY 무게).

phase3_002 가 이 누락을 실측으로 확인했다 (§0: 15-streak). 본 proposal 은 이를
다루되, 분석 과정에서 L3 의 진단을 한 단계 더 정정한다 (§2).

---

## 2. L3 재진단 — "다양성 강제 부족" 아래의 "표면 인식 부재"

phase3_002 의 채택 코드 (`workspace/transcribe.py`) 와 실제 backend 표면
(`frozen/asr_backend.py`) 을 대조하면 L3 의 표면적 진단("같은 fingerprint 반복
허용")보다 더 근본적인 원인이 보인다.

- `frozen.asr_backend.load()` 는 **raw CT2 Whisper 모델 + processor 를 그대로
  반환** 한다. 즉 candidate 는 `model` 의 모든 메서드·kwarg 에 (ct2 직접 import
  없이) 닿을 수 있다.
- 그러나 phase3_002 25 iter 동안 candidate 는 `generate(beam_size, temperature,
  length_penalty, patience)` 만 변주했다. `model.align(...)` (word timestamp),
  `generate(return_scores=True)` (confidence), `generate(return_no_speech_prob
  =True)` (no-speech), temperature **list** fallback, prefix 조건화 등은 **한 번도
  시도되지 않았다.**

→ 막힌 진짜 원인은 "다양성 강제 부족" 이전에 **에이전트가 자기 표면을 모른다**
는 것. 작은 파라미터 공간을 다 훑고도 큰 lever 는 *존재 자체를 인식 못 함*.

이 진단이 두 가지 처방 철학을 가른다.

### 2.1 처방형 (prescriptive) — 원 skeleton 방향

harness 가 "원점 재고하라 / 다른 lane 으로 가라 / 이 메트릭을 봐라" 를 **지시로
주입**. 시도할 mechanism 을 *사람이 정의* 해 메뉴로 먹인다 (§4 의 §3.1~3.4).

- 한계: 우리가 *아는* mechanism 만 탐색 대상이 된다. align/score 같은 걸 우리가
  메뉴에 안 넣으면 에이전트도 영원히 못 씀. 또 capability map 을 떠먹이는 순간
  candidate 의 *발견 능력* 자체는 측정·육성되지 않음 (선행 RFC §2.2 (b) 의 취지와
  충돌).

### 2.2 발견형 (discovery-first) — 본 개정 권고

harness 는 **"표면이 미탐사다" 라는 사실과 그 위치만 지목** 하고, 무엇이 거기
있는지는 비운다. mechanism 은 에이전트가 `frozen` 표면을 직접 더듬어 *발견* 한다.

- 장점: 우리가 모르는 capability 도 후보가 됨. candidate 의 정찰·가설 능력이
  곧 성능 동력이 되어 측정·육성됨. capability 이름(align 등)을 prompt 에 박지
  않으므로 "정답 누수" 도 없음.
- 핵심 전제: candidate 는 `frozen/asr_backend.py` 읽기가 금지돼 있지 않다 (현
  profile 의 hard constraint 는 harness/judge/baseline 읽기만 금지). 즉 발견의
  정문이 이미 열려 있는데 prompt 가 안 가리킬 뿐이다.

**권고: 발견형(§3) 을 primary 로 채택, 처방형(§4) 은 보완/트리거 스위치로 유지.**

---

## 3. 발견형 설계 (primary)

### 3.1 표면 재지목 — `candidate.md` Role/Approach 교체

"5 lane 중 하나 골라 한 가지 바꿔라"(처방) 를 "네 표면은 미탐사다, 가서 매핑해
무엇이 가능한지 *발견* 하라"(탐사) 로 바꾼다. capability 이름은 주지 않는다.

```markdown
## Your real surface is undermapped

You reach the model only through `frozen.asr_backend`. Read it. Whatever
`load()` returns, and whatever that object supports, is your true surface —
and you have used a tiny fraction of it. The obvious parameter tweaks are
exhausted. The remaining headroom is in capabilities of the backend you have
not yet discovered or used. You are not told what those are. Finding them is
the work.
```

### 3.2 정찰을 출력의 1급 시민으로 — 새 YAML 필드

매 iter 코드 수정 *전* 정찰을 강제. diff 가 아니라 *발견* 을 운반하게 한다.

```yaml
capability_investigated: <이번 iter 에 더듬은 backend 표면 부위>
what_i_learned: <표면에 대한 구체적 사실 — 음성 결과여도 기록>
hypothesis: <단일 변경 + 이 발견이 왜 그 변경을 동기화하는지>
fingerprint: [token, ...]   # 1-6, dedup 용
```

- `lane` 필드는 *선택적 태그* 로 강등 (round-robin 강제 폐기, §4.2 와 연동).
- `what_i_learned` 가 핵심 — 파라미터 스윕이 아니라 "표면을 한 조각 더 알아내라"
  를 매 iter 강제.

### 3.3 발견의 롤백 생존 — findings ledger

현 구조: reject = `transcribe.py` 롤백 = **학습 소멸**. 25 iter 돌아도 표면 지식이
안 쌓인다 (candidate 는 최근 5 iter + best diagnosis 만 봄).

→ `runner.build_candidate_prompt` 가 직전 N iter 의 `what_i_learned` 를 모아
다음 prompt 에 **누적 주입**. 코드는 롤백돼도 *발견은 살아남아 복리로 쌓임*.

- 저장: `runs/<hyp_id>/candidate_meta.json` (A' 정본) 에 새 필드 포함 → runner 가
  최근 ledger 로 집계.
- 주의: ledger 가 anchoring 으로 역작용하지 않도록 "사실(facts)" 만 담고
  "결론(prescriptions)" 은 배제. §4.1 처방형 cold-restart 발동 시엔 ledger 도
  최소화 (L2 망각 모드와 연동).

### 3.4 plateau → 탐색 반경 확대 (mechanism 미지정)

best 미갱신 streak ≥ K 면 prompt 에 "파라미터 튜닝은 끝났다, 다음 이득은 *아직
안 쓴 mechanism* 에서 온다, 구조가 달라지는 것을 두려워 말라 — 단, 그 mechanism
이 뭔지는 네가 표면에서 찾아라" 를 주입. **메커니즘을 지정하지 않는다.** "one
focused change / no refactor" 규칙은 이 모드에서만 완화.

- sub-threshold 누적 구제(§0 의 iter12/18 문제): 같은 방향 sub-2σ 이득이 M회
  연속이면 누적 채택하거나, plateau 후반엔 게이트를 2σ→1σ 로 완화하는 정책 옵션
  (별도 검토 — harness 결정 로직 변경).

### 3.5 (선택) 경험적 probing — candidate 에 shell 이 있을 때

candidate 가 Bash 등 실행 도구를 가지면 "표면을 *읽어서 추론* " 을 넘어 "*실행해
확인* " 가능: `dir(model)`, `help(...)`, 작은 probe 스크립트로 반환 구조 검증.
발견형의 폭이 질적으로 커진다.

- 전제: production runner 의 candidate hardening(`--disallowedTools`) 상태에 의존.
  shell 차단 시 §3.5 는 비활성, §3.1~3.4 만으로 동작.
- 가드: probe 가 holdout/forbidden 경로에 닿지 않도록 기존 deny 훅 유지.

---

## 4. 처방형 메커니즘 (원 skeleton — 보완/트리거로 유지)

원 skeleton §3 의 메커니즘은 폐기하지 않는다. 발견형의 **트리거·스위치** 로
재해석한다: 아래 트리거가 발동하면 §3 의 *발견 모드* 를 켠다.

### 4.1 Cold-restart wildcard → 발견 모드 스위치

`state.iters_since_best_update >= COLD_RESTART_THRESHOLD` 면 prompt 가 발견 모드
(§3.4) 로 전환 + HISTORY tail / best diagnosis 생략 (§4.3).

- threshold 후보: 5 / 7 / 10 (phase3_002 streak=15 분포상 5~7 권장).

### 4.2 Lane-cluster jump → lane round-robin 폐기로 대체

발견형에서 `lane` 은 태그로 강등(§3.2)되므로 round-robin 강제 자체가 불필요.
대신 `fingerprint` 중복만 dedup 신호로 사용. (원 skeleton §3.2 의 "강제 제외 set"
은 fingerprint 기준으로 흡수.)

### 4.3 HISTORY 망각 모드

cold-restart(§4.1) 발동 시 동반. `_history_tail` 최소화(best line 만) + findings
ledger(§3.3) 도 facts only 로 축소. 이전 narrative anchoring(L2) 차단.

### 4.4 새 관점 메트릭 reframe

diagnosis 가 같은 metric(length_ratio/hallucination) 만 반복 인용하면 안 본 axis
(speaker turn / SNR / music ratio 등) 를 강제 노출. **단 `judge/` 수정 필요 = 평가
정의 변화** 라 별도 큰 결정 (원 skeleton §9 와 동일 유보).

---

## 5. 판정 — phase3_002 적용

원 skeleton §4 판정표에 phase3_002 실측 대입:

| phase3_002 결과 | 도입 | 본 잡 해당? |
|---|---|---|
| lane entropy > 1.2 AND streak ≤ 2 | 불필요 (Rejected) | — |
| 0.8 < entropy < 1.2 OR streak 3~4 | 부분 (cold-restart 만) | — |
| **entropy ≤ 0.8 OR streak ≥ 5** | **전면 (§3 발견형 전면 + §4 트리거)** | **✅ streak=15** |
| LLM 권고 lane 무시율 > 60 % | lane-cluster jump 우선 | <!-- REPORT 확인 --> |

→ **전면 도입**. 단 "전면"의 내용은 §3 발견형 우선, §4 는 트리거/보완.

판정 데이터:
- lane / fingerprint per-iter 원자료: `runs/<hyp_id>/candidate_meta.json` —
  **이번 잡은 부재** (gitignore + 미보존). entropy/Jaccard/무시율 측정 불가.
- 따라서 판정은 entropy 가 아닌 **streak=15** 단독 근거. entropy 기반 세부 분기
  (예: "entropy>1.2 면 불필요")는 다음 잡부터 `candidate_meta` 보존 후 적용 (§7).
- 집계 도구: `scripts/analyze_run.py` D 축 + `docs/reports/phase3_002_REPORT_*.md`
  (이번엔 runs 0개로 빈 산출).

---

## 6. 대안 / 기각

- **(a) phase3_002 에서 미리 도입** — 이미 지나감. phase3_003 이 본 proposal 의
  첫 측정 잡 (phase3_002 와 직접 ablation 비교 불가, 새 baseline).
- **(b) round-robin 처음부터 강제** — 기각 (선행 RFC §2.2 (d)). 발견형에서는
  lane 강등으로 자연 해소.
- **(c) capability map 을 prompt 에 직접 박기 (순수 처방형)** — 기각. §2.1 한계
  (정답 누수 + 발견 능력 미측정). 단 발견형이 phase3_003 에서도 표면을 못 찾으면
  *부분 힌트* (위치보다 한 단계 구체적인 단서) 로 후퇴하는 fallback 으로 보류.
- **(d) 외부 randomness (seed-based wildcard)** — 후속 옵션.

---

## 7. 작업 계획

- [x] phase3_002 결과 분석 → 본 proposal 본문 (§0/§2 발견형 재진단)
- [ ] **분석 인프라 갭 수정**: `candidate_meta.json` (lane/fingerprint/발견필드)
      을 gitignore 에서 제외하거나 `runs/_summary/` 로 집계 보존 → 다음 잡부터
      analyze_run.py D축 (entropy/Jaccard) 이 실제로 채워지게. (phase3_002 는
      runs 0개로 D축 측정 불가였음 — §0 ⚠️)
- [ ] `candidate.md`: Role/Approach 를 발견형(§3.1)으로 교체, 출력 YAML 을
      §3.2 필드로 교체, plateau 모드(§3.4) 섹션 신설
- [ ] `runner.build_candidate_prompt`: findings ledger 주입(§3.3),
      cold-restart 트리거(§4.1), HISTORY 망각(§4.3); round-robin 제거(§4.2)
- [ ] `state.py`: `iters_since_best_update`, (필요시) recent `what_i_learned`
      추적 — 직렬화 하위호환 명시
- [ ] `analyze_run.py` D 축: 발견 모드 발동 횟수 / capability 신규성 측정 추가
- [ ] phase3_003 (phase3-explore 브랜치) 으로 효과 측정

---

## 8. 영향 받는 파일

### 수정
- `harness/prompts/candidate.md` — Role/Approach 발견형 교체, 출력 필드 교체,
  plateau/발견 모드 섹션
- `harness/runner.py` — `build_candidate_prompt` (ledger, 트리거, round-robin 제거)
- `harness/state.py` — 신규 추적 필드 (하위호환)
- `scripts/analyze_run.py` — 발견 모드 효과 메트릭
- `judge/diagnosis.py` — (§4.4 채택 시만) metric reframe. 평가 정의 변화라 별도 결정

### 신규
- 없음 — 기존 파일 확장

---

## 9. 가드레일 검토

| 대상 | 변경 |
|---|---|
| candidate sandbox | §3.5 미채택 시 없음 (prompt 변경만). §3.5 채택 시 shell 노출 → 기존 holdout/forbidden deny 훅으로 차단 유지 (신규 가드 불요, 기존 재확인) |
| state.py 직렬화 | 신규 필드 — 하위호환 (없으면 0/empty default) |
| ablation 비교 | phase3_003 은 phase3_002 와 직접 비교 불가 — 새 baseline |
| 정답 누수 | 발견형은 capability 이름을 prompt 에 안 박음 → SPEC §11 라벨 주입 금지와 정합 |

---

## 10. 미결 사항

- COLD_RESTART_THRESHOLD 적정값 (phase3_002 streak=15 → 5~7 후보, REPORT 분포 확인)
- findings ledger 깊이 N 과 "facts only" 필터 강도 (anchoring 역작용 방지)
- sub-threshold 누적 구제(§3.4) 를 게이트 정책으로 넣을지 — harness 결정 로직 변경
  이라 별도 검토
- §3.5 경험적 probing 활성 여부 — candidate hardening 정책에 의존 (운영자 결정)
- §4.4 metric reframe 의 `judge/` 수정 — 평가 정의 변화 = 큰 결정, 별도 RFC
