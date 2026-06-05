# Phase 3 회고 — phase3_004 (50-iter 발견형 루프)

| | |
|---|---|
| 작성 | 2026-05-30 12:30 KST |
| 대상 잡 | `phase3_004` (branch `phase3-run50`, iter-0 stub 시작, 50 iter) |
| Ground truth | git commit (`iterN: keep/reject`) + `runs/_summary/phase3_004_state.json` |
| 결과 | eval `corpus_cer` **0.1574** (iter_018) / holdout **0.1847** / baseline 0.4685 |
| 목적 | 다음 브랜치 시도의 토대 — 좋은 점·아쉬운 점·개선 포인트 |

> **주의**: `docs/reports/phase3_004_REPORT_2026-05-30.md` (analyze_run 자동산출)의
> accept-detection 은 git ground truth 와 불일치한다 (아래 아쉬운 점 #4). 본 회고의
> keep 집합·궤적은 git/state 기준.

---

## 0. 실제 banked 궤적 (git 기준, keep 5개)

| keep | hyp_id | corpus_cer | Δ | 발견/메커니즘 |
|---|---|---|---|---|
| 1 | iter_002 | 0.4104 | (stub 대비) | stub 은 30s 한 윈도우만 디코드 → 멀티분 통화의 30s 이후 전량 삭제. 순차 청킹 루프 |
| 2 | iter_004 | 0.4046 | -0.006 | greedy(beam=1)의 repeat→early-EOT 붕괴 진단 → beam search |
| 3 | iter_006 | 0.3322 | -0.078 | `<|notimestamps|>` 가 선택적임을 발견 → timestamp 모드로 디코드 |
| 4 | **iter_007** | **0.1611** | **-0.171** | **디코드 시퀀스가 per-segment end timestamp 토큰을 반환 → 동적 윈도우 seek. 커버리지 병목 해소 (length_ratio 0.59→0.95)** |
| 5 | **iter_018** | **0.1574** | **-0.0037** | `scores[0]`(avg-logprob) + zlib `compression_ratio` 품질 게이트 → temperature fallback 재디코드. **ε=0.002 banking 으로 채택** |

이후 iter_019~050 (32 iter, 64%) 전부 reject — best 0.1574 정체.

---

## 1. 좋은 점 (검증된 것)

1. **발견형(discovery-first) 루프가 실제로 작동.** 후보가 frozen 표면을 자율
   탐사해 50 iter 동안 ~40개의 서로 다른 backend capability 를 *스스로* 찾아냄:
   timestamp 토큰 반환, `no_speech_prob`, `return_scores`/avg-logprob,
   compression_ratio, `num_hypotheses` 빔 리스트, `align()`/`text_token_probs`
   (음향 신뢰도), `detect_language`, `suppress_tokens`/`suppress_blank`,
   `max_initial_timestamp_index`, energy-VAD, batched multi-prompt 등. 스푼피딩
   없이 표면에서 끌어냄.

2. **큰 도약은 파라미터가 아니라 *구조 발견*에서 나옴.** 최대 이득 iter_007
   (Δ0.171)은 beam 튜닝이 아니라 "디코드가 timestamp 토큰을 반환한다"는 표면
   사실의 발견 → 동적 윈도잉. 커버리지(절반만 받아쓰던 long-form)를 단번에 해소.

3. **Banking(ε=0.002) 정상 작동, 의도대로.** iter_018 의 작은 실질이득
   (Δ0.0037)은 옛 0.01 게이트면 버려졌을 것. ε=0.002 로 keep+best 갱신 →
   local-min escape 에 직접 기여. (리뷰 F2 수정의 효과 입증.)

4. **구조 개선이 holdout 으로 전이.** hallucination_hit_rate ~0, length_ratio
   0.95 가 unseen 16파일에서도 유지. holdout 0.1847 은 baseline 0.4685 의 40%.
   eval 한정 트릭이 아니라 진짜 long-form 파이프라인 개선.

5. **누적 발견(F1 ledger)으로 복리 추론 발생.** 후보가 이전 발견을 명시 참조해
   조합 시도: iter_021 "iter_008 conditioning + iter_018 gate composable",
   iter_036 "iter_023 longest-beam + iter_032 align 가 상보적으로 상쇄". sweep
   아닌 누적 탐색. (fingerprint Jaccard 평균거리 0.958.)

---

## 2. 아쉬운 점

1. **긴 plateau — 64% 비용이 정체 구간.** iter_018 이후 32 iter 전부 reject.
   후보는 영리한 조합(ROVER 투표 iter_033, medoid consensus iter_031, align-trim
   iter_037, dual-prompt iter_043)을 계속 냈지만 어느 것도 0.157 을 못 깸.

2. **병목이 frozen 표면 *밖*으로 이동.** best 코드의 error_breakdown 은 sub
   지배적(sub≈0.58 / del≈0.35 / ins≈0.06). 즉 잔여 오류는 **치환** — 도메인 용어
   오인식 + 전화음질(8kHz) 오청취. 이건 청킹/디코딩 트릭으로 안 풀린다. 후보도
   자각(iter_030: "모든 이전 수정이 linguistic 이거나 acoustic 인데 residual 이
   남는다"). **표면이 줄 수 있는 헤드룸을 거의 소진.**

3. **diagnosis→hypothesis 변환이 약함.** focus_file 이 같은 2개
   (`00003092…`, `00003011…`)를 37회/31회 반복 지목했지만 best 는 그 신호를 끝내
   살리지 못함. 진단이 "어느 파일"은 짚어도 "왜 안 풀리나"로 후보를 이끌지 못함.

4. **`analyze_run` 리포트 accept-detection 버그.** 리포트는 keep 3개
   (iter_002/006/007)로 집계하나 git 실제는 5개 (iter_004·iter_018 누락/오표기).
   결과적으로 리포트의 D(다양성)·G(attribution) 축이 *틀린 accept 집합* 위에
   계산됨. **다음 잡 전 수정 필요** — git commit 메시지를 ground truth 로 파싱.

5. **A' lane 메타 미사용 → 다양성 관측 저하.** lane 자기선언이 0 이라 리포트
   lane entropy "n/a", fingerprint Jaccard 하나에만 의존 (리뷰 F5 미해결).

6. **holdout overfit 플래그 YES** (Δ+0.0273 > 0.01). 절대치는 우수하고 이전
   phase3_002(Δ+0.039)보다 개선됐지만, eval 11파일 banking 이 소폭 과적합 유발.
   현재 holdout 체크가 *잡 종료 후* 만이라 진행 중 감시 불가.

7. **비용.** wall 26,323s(~7.3h), 채택당 8,774s, Δcer/min 0.00057. plateau
   구간이 사실상 비용만 소모(조기 종료 장치 없음).

---

## 3. 개선 포인트 (다음 브랜치)

### 3.1 도메인 치환 공략 — 가장 큰 잔여 레버
- **선행 측정**: best 출력을 라벨과 정렬해 sub 에러가 *도메인 용어*에
  클러스터링되는지 확인. 일반어/오청취가 지배적이면 아래 A/B 모두 무의미.
- **A안 (규칙 내, 후보 가능):** 사후 어휘 교정 — 한국 보험 lexicon(해지환급금,
  특약, 면책, 고지의무, 피보험자, 청약철회…)을 자모 편집거리/발음유사도로 매칭해
  high-confidence 근접오류만 교정. 정밀도 우선(과교정 가드). 후보 발견 힌트로
  심거나 직접 구현.
- **B안 (표면 확장, harness 변경):** `frozen.asr_backend.generate` 에
  hotword/logit-bias 통로 노출 → 진짜 vocabulary biasing. 후보 자율범위를 넘는
  변경이라 별도 RFC.

### 3.2 plateau 조기 종료/전환
- `iters_since_best_update` 가 임계(예: 8)를 넘으면 (a) 자동 종료 또는
  (b) 강제 표면확장/도메인 모드 전환. 64% 낭비 방지.

### 3.3 harness 수정
- `analyze_run` accept-detection 을 git ground truth 기반으로 교정 (D/G 재계산).
- lane 메타 부활 or `capability_investigated` 텍스트 자동 클러스터링으로 다양성
  관측 복구 (F5).

### 3.4 diagnosis 루프 강화
- 반복 focus 파일에 대해 후보가 "이 파일이 왜 안 풀리는지"를 명시 분석하도록
  프롬프트 — 진단을 가설로 변환하는 압력 추가.

### 3.5 평가/봉인
- primary metric 재검토: corpus vs macro Δ mean 0.014, per-file std 0.066 —
  한두 파일이 끌고가는지 trimmed mean 고려 (E축).
- banking 과적합 감시: 잡 중간 holdout 체크포인트 도입 검토.

---

## 4. 한 줄 결론

발견형 루프 + ε banking 으로 stub(0.41) → **eval 0.157 / holdout 0.185** 달성,
환각 제거·커버리지 0.95 로 **baseline(0.4685)을 압도**. 진화 메커니즘은 검증됨.
단 잔여 병목이 **도메인 치환**으로 이동했고 이는 *현 frozen 표면 밖* — 다음
시도의 핵심은 (1) 치환이 도메인 용어인지 측정, (2) 어휘 교정(규칙 내) 또는
표면 확장(RFC), (3) plateau 조기 전환 + analyze_run accept 버그 수정.
