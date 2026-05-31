# Phase 3 회고 — phase3_005 (120-iter, 70+50 이어붙임)

| | |
|---|---|
| 작성 | 2026-05-31 KST |
| 대상 잡 | `phase3_005` (branch `phase3-diagnosis-feedback`, 70 iter → 이어서 +50, 총 120) |
| Ground truth | git commit (`iterN: keep/reject`) + `runs/_summary/phase3_005_state.json` |
| 결과 | eval `corpus_cer` **0.1775** (iter_063) / baseline 0.4685 |
| 비교 | **phase3_004 = 0.1574 (50 iter)** — 더 적은 iter 로 더 낮음 |
| 목적 | 왜 50회(0.157)보다 나빴나 + 다음 시도의 토대 |

> 실험 로그 보존: `docs/history-archive/runs/phase3_005_runs_sanitized.tar.gz`
> (콜ID·마스킹폰·batch·홈경로 0 검증 — `scripts/sanitize_runs.py`).

---

## 0. banked 궤적 (git 기준, keep 7개)

| keep | hyp_id | corpus_cer | Δ | 발견/메커니즘 |
|---|---|---|---|---|
| 1 | iter_003 | 0.5628 | (seed 대비) | `<|startofprev|>` prev-conditioning + longform 청킹 |
| 2 | iter_005 | 0.4275 | -0.135 | timestamp 토큰 모드 + adaptive seek |
| 3 | iter_006 | 0.1901 | **-0.237** | repetition-penalty + no-repeat-ngram + beam/patience (decode hygiene) — **최대 도약** |
| 4 | iter_009 | 0.1877 | -0.0024 | domain-prior + synthesis + insertion-guard |
| 5 | iter_049 | 0.1835 | -0.0042 | ngram-four + isolate (exploit-b) |
| 6 | iter_059 | 0.1808 | -0.0027 | beam-width + patience + search-depth |
| 7 | **iter_063** | **0.1775** | **-0.0033** | **channel-eq + spectral-envelope (오디오 프론트엔드)** — plateau 깬 유일 |

iter_064~120 (57 iter, 48%) 전부 reject. `iters_since_best_update=57`.

---

## 1. 좋은 점

1. **다양성은 충분 — 9개 방법 계열 자율 탐색.** decode 파라미터, 오디오
   프론트엔드(preemphasis/compand/channel-eq/cmn/cmvn/vtln/log-mel),
   timestamp/seek, 도메인 프롬프트, temperature 재디코딩, n-best/MBR/rover,
   forced-alignment, VAD, vocab suppress-mask. 폭은 phase3_004 못지않음.

2. **verify hang 자동 방어가 실전에서 작동.** iter_008(suppress-tokens latin-mask)이
   디코더 EOT 를 막아 2-3h hang → mid-run 에 추가한 wall-clock 타임아웃
   (`verify.py`, cap+여유 ~21분)이 latin-mask 계열(iter11/67 등)을 자동 사살.
   타임아웃 없었으면 밤새 멈췄을 것.

3. **explore→exploit 스케줄·synthesis 기계는 정상.** 초반 다양 탐색, 후반
   exploit-b/isolate 재조합, best(iter_063)가 후반에 등장. 메커니즘 자체는 죽지 않음.

4. **decode hygiene 의 큰 이득 재확인.** iter_006(repetition+ngram+beam)이
   0.43→0.19, Δ0.237 — 단일 최대 도약. 기본기의 가치.

---

## 2. 아쉬운 점

1. **70 iter 더 들이고 50 iter(0.157)보다 나쁨 (0.1775).** 핵심은 **경로 의존성**.
   phase3_004 는 "timestamp 동적 윈도잉(0.161) → 반복제어 → 조건부 temperature
   재디코딩(0.157)"을 생산적 순서로 쌓았다. phase3_005 는 decode-hygiene 로 0.19
   plateau 에 먼저 갇혔고, **004 의 우승 기법(조건부 재디코딩)을 iter_010·046·058 에서
   시도했으나 베이스가 달라 트리거(scores<-1.0, compression>2.4)가 안 걸려 전부 reject**.
   같은 기법이 잘못된 토대 위에선 무력. 004 의 iter_007 급 구조적 도약을 끝내 못 만듦.

2. **banking 임계값 0.002 가 후반 진전을 직접 차단.** iter_071~120 에서 best(0.1775)를
   **실제로 이긴** 후보가 4개 있었다 — 전부 **외국어 스크립트 토큰 suppress**:

   | iter | CER | Δ | 결과 |
   |---|---|---|---|
   | 098 | 0.1769 | 0.0006 | reject (Δ<0.002) |
   | 105 | 0.1772 | 0.0003 | reject |
   | 111 | 0.1769 | 0.0006 | reject |
   | 113 | 0.1772 | 0.0003 | reject |

   전부 Δ<0.002 라 banking floor 에 막혀 reject → best 안 바뀜 → 다음 후보는
   0.1769 가 아니라 0.1775(iter_063) 위에서 다시 시작 → **개선이 누적되지 않음**.
   harness 가 더 나은 방법을 4번 찾고도 매번 버린 셈. 지금 병목은 아이디어가 아니라
   banking 게이트.

3. **explore floor 0.5 는 plateau 를 깨는 레버가 아니었다.** 70→120 이어붙이며 floor
   0.2→0.5 로 올렸으나 0 keep. 더 다양해졌지만 대부분 **악화**: audio-frontend
   (cmn/cmvn/vtln/log-mel) 0.20~0.22, **forced-alignment(align)은 0.49 로 전사 파탄**
   (iter_084·112). 그리고 후반은 suppress-foreign-token 에 mode-collapse(~13 iter).
   탐색을 늘려도 좋은 한 방(suppress-foreign)은 이미 찾았고 그걸 막은 건 banking 이었다.

4. **방법 과편중 + dead-end 반복.** decode-param + audio-frontend 가 59%(70/120 중)를
   차지. latin/foreign suppress-mask 는 iter_008/011/067/098… 반복 시도 — findings
   ledger 가 멀리 떨어진 재시도를 못 막음. verify-empty(crash/timeout) 15 iter.

5. **seed 가 약했다.** iter_001~006 에서 0.56→0.19 로 scratch 재발견에 6 iter 소모.
   004 의 0.157 산출물을 seed 로 썼다면 그 토대 위에서 출발 가능했다.

---

## 3. 개선 포인트 (다음 시도)

1. **banking 0.002 → ~0.0005.** deterministic eval 이라 σ 잠정 — 실질 sub-0.002
   개선이 노이즈가 아니라 진짜다. suppress-foreign(0.1769)이 bank 되면 그 위에 누적돼
   0.176→0.174… 추가 하강 여지. **가장 싸고 직접적.**
2. **004 iter_018(0.157) 을 seed 로 고정.** 구조가 더 낮은 토대에서 출발. 단
   `best_cer` 를 초기 state 에 명시해야 첫 후보 auto-keep 으로 floor 가 날아가지 않음
   (`policy.decide_candidate`: `best_cer is None → 무조건 keep`).
3. **explore floor 는 0.3 으로 회귀.** 0.5 는 해로운 후보를 더 부르고 plateau 를 못 깸.
4. **findings ledger 를 전 구간 dedup.** 최근 window 만이 아니라 잡 전체의 fingerprint 로
   재시도 차단(latin/foreign-mask 3회 반복 방지).
5. **미탐색 구조 레버**: 2-pass decoding, 외부 LM rescoring, 한국어 post-edit(도메인
   사전 교정), segment-level VAD 재분할 — decode-knob/audio-frontend plateau 밖.

---

## 4. 하네스 개선 (이번 잡 중 반영됨)

- `verify.py`: judge.evaluate 에 wall-clock 타임아웃(cap+여유) 추가 — 사후 측정
  게이트가 못 잡던 디코딩 hang 을 강제 종료 (commit `8eff14c`).
- `config.py`: `EXPLORE_RATIO_FLOOR` 0.2→0.5 (commit `fa4e48e`) — 본 회고 #3 기준
  되돌림 검토 대상.
- `scripts/sanitize_runs.py`: 실험 로그 PII-safe 아카이브(콜ID/마스킹폰/batch/홈경로
  제거 + 스캐너 검증) — 본 회고의 보존 아카이브 생성에 사용.
