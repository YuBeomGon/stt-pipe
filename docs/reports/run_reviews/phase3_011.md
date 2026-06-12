# phase3_011 — run 회고

## 개요

- **run**: phase3_011 (Gen6 하네스, phase3-family-lineage 브랜치 — lineage family + near-best 풀 + plateau burst), 2026-06-02 종료, **24 iter**.
- **지표**: corpus_cer (낮을수록 좋음). baseline stub=0.4126, faster-whisper 앵커=0.1714.
- **결과**: 최종 champion **CER 0.180724 (iter 21)**. stub 대비 −0.2319 개선, 앵커(0.1714)에는 미달.
- **궤적 요약**: iter 2~12 에서 0.4092 → 0.1850 까지 가파르게 하강한 뒤, iter 13~23 의 11 iter 동안 0.18 부근 plateau — keep 은 iter 18(+0.0021), 21(+0.0021) 단 두 번. 이 run 의 **조기수렴**이 Gen6 의 "plateau → 주기적 burst + 조합 모드, discovery floor 재조정" 도입 계기가 됐다 (docs/HARNESS-EVOLUTION.md Gen6 절).
- keep 10 / reject 13 / 미채점-종료 1. 미채점 5건: CUDA OOM 3건(verify 중 judge.evaluate 비정상 종료), scope 위반 1건, run 종료로 평가 미실행 1건.

## Iteration 기록

| iter | mode | 시도한 것 (한 줄) | CER | 판정 |
|---|---|---|---|---|
| 001 | explore | 30s 윈도우 분할 + 단일 batched generate 로 장음성 tail 복구 | — (CUDA OOM, 채점 실패) | reject |
| 002 | repair | 윈도우 유지하되 4개씩 sub-batch 디코드로 OOM 해소 | 0.409189 | keep (first valid) |
| 003 | refine | greedy → beam_size=5 + patience=2.0 + length_penalty=1.1 + no_repeat_ngram=3 | 0.355218 | keep (Δ+0.0540) |
| 004 | explore | timestamp 기반 segment-end 까지만 채택 + 가변 seek 전진 | — (scope 위반: harness/runner.py) | reject |
| 005 | explore | `<startofprev>` prev-text 컨디셔닝으로 윈도우 경계 deletion 복구 | — (CUDA OOM) | reject |
| 006 | repair | prev-text 유지 + _BATCH_WINDOWS=2 / context 100tok 으로 VRAM 절감 시도 | — (CUDA OOM) | reject |
| 007 | repair | 윈도우당 1개 순차 디코드 + prev-context 64tok 트림으로 OOM 최종 해소 | 0.346810 | keep (Δ+0.0084) |
| 008 | combine | family_003(prev-text)+family_001(beam) 접합 + 4s overlap 윈도우 + dedup | 0.309072 | keep (Δ+0.0377) |
| 009 | ablate | no_repeat_ngram_size=3 제거 — 점수 유지하며 디코드 단순화 | 0.304524 | keep (Δ+0.0045) |
| 010 | explore | 고정 stride → timestamp-driven seek (마지막 segment-end 로 전진, 최소 15s floor) | 0.225241 | keep (Δ+0.0793, 최대 단일 개선) |
| 011 | refine | length_penalty 1.1→1.0 (중립 normalization) | 0.202063 | keep (Δ+0.0232) |
| 012 | explore | compression-ratio>2.4 / avg_logprob<−1.0 게이트 + temperature-fallback 재디코드 | 0.185003 | keep (Δ+0.0171) |
| 013 | refine | _PREV_CONTEXT_TOKENS 64→128 (substitution 축 공략) | 0.188427 | reject (Δ−0.0034) |
| 014 | combine | prev-context 128 을 quality-gate 통과 윈도우에만 시드 (gate-passed prefix) | 0.189943 | reject (Δ−0.0049) |
| 015 | explore | no_speech_prob>0.6 침묵 게이트 — 텍스트 억제 + prior 오염 차단 | 0.185029 | reject (Δ−0.0000) |
| 016 | refine | beam_size 5→8 확대 | 0.188218 | reject (Δ−0.0032) |
| 017 | ablate | temperature ladder + quality gate 통째 제거 (단일 beam 디코드) | 0.202063 | reject (Δ−0.0171, ladder 가 유효함을 역증명) |
| 018 | refine | gate-passed 시드 가드 유지 + prev-context 128→64 회귀 | 0.182859 | keep (Δ+0.0021) |
| 019 | explore | detect_language 로 `<\|ko\|>` 확률 게이트 — 비음성 윈도우 hallucination 차단 | 0.183303 | reject (Δ−0.0004) |
| 020 | ablate | avg-logprob floor 제거 — compression-ratio 단독 게이트 | 0.187556 | reject (Δ−0.0047) |
| 021 | refine | temperature ladder 상단 (0,0.4,0.8)→(0,0.2,0.4) 축소 | 0.180724 | keep (Δ+0.0021) |
| 022 | combine | family_006 의 ko-확률 게이트(floor 0.35) 를 best 파이프라인에 접합 | 0.182179 | reject (Δ−0.0015) |
| 023 | refine | _BEAM_PATIENCE 2.0→3.0 | 0.183547 | reject (Δ−0.0028) |
| 024 | explore | beam 상위 2개 일치 prefix 만 prev-text prior 로 시드 | — (run 종료, 평가 미실행: per_file 0건·telemetry 빈 디렉터리·HISTORY 항목 없음) | 미판정 |

판정/Δ 출처: `temp/history_docs/branch_history/HISTORY.phase3-family-lineage.md` phase3_011 절 (keep 기준 Δcer ≥ 0.002).

## CER 궤적

```
iter  02    03    07    08    09    10    11    12    13    14    15    16    17    18    19    20    21    22    23
cer  .409  .355  .347  .309  .305  .225  .202  .185  .188  .190  .185  .188  .202  .183  .183  .188  .181  .182  .184
            ↓gradual         ↓——— 급강하 (10~12) ———↓     ←———————— plateau ~0.18 (13~23) ————————→
```

- champion 갱신: 0.4092(2) → 0.3552(3) → 0.3468(7) → 0.3091(8) → 0.3045(9) → 0.2252(10) → 0.2021(11) → 0.1850(12) → 0.1829(18) → **0.1807(21)**.
- iter 12 이후 11 iter 에서 누적 개선 −0.0043 뿐. 채점된 23 iter 중 최저는 곧 최종 champion 0.180724.

## 무엇이 점수를 만들었나

1. **장음성 커버리지 복구가 개선의 8할**: 30s 절단 deletion 을 잡은 windowing(iter 2)에서 시작해, timestamp-driven seek(iter 10, Δ+0.0793 단일 최대) 까지 — 0.41→0.225 구간은 전부 "잘린 오디오를 다시 듣게 만들기"였다.
2. **디코드 탐색 튜닝이 2할**: beam search(3), overlap+dedup combine(8), length_penalty=1.0(11), temperature-fallback 게이트(12)로 0.225→0.185.
3. **combine/ablate 모드가 실질 기여**: iter 8 combine 은 두 family 의 장점 접합으로 −0.038, iter 9·17 ablate 는 "ngram 제약은 불필요 / ladder 는 필요"를 각각 확정해 탐색 방향을 정리했다.
4. **OOM repair 사이클이 family_003 을 살림**: iter 5→6→7 의 연쇄 repair (batched→batch2→sequential)가 prev-text 컨디셔닝을 끝내 채점대에 올렸고, 이게 iter 8 combine 의 한 축이 됐다.

## 한계와 보완점

- **조기수렴**: iter 13~23 은 substitution 축(~50%)을 11가지 방법으로 찔렀지만 keep 2건 합계 −0.0043. explore 신규 발상(15·19)도 champion 을 못 이겼다 — 이후 phase3_014 분석("explore 단독으론 못 이김")과 일치하는 전조. 이 run 이 Gen6 plateau-burst·discovery floor 재조정의 직접 계기.
- **앵커 미달**: 0.1807 vs faster-whisper 0.1714 — substitution 병목을 디코드 노브로는 못 깬다는 신호.
- **초반 OOM 3회 낭비**: batched generate 의 VRAM 한계를 iter 1·5·6 세 번 반복 학습. 메모리 가드/사전 경고가 하네스에 없었다.
- **scope 위반 1건**(iter 4, harness/runner.py 수정 시도): 가드는 작동했으나 timestamp-seek 아이디어 자체는 유효했고 iter 10 에서 재발견되기까지 6 iter 소요.
- **iter 24 증발**: 후보 생성까지 끝난 상태에서 평가 없이 run 종료 — 마지막 iter 예산 처리(생성-평가 원자성)가 허술.

## 아티팩트

- iter 데이터: `runs/_archive/phase3_011_iter_001` ~ `phase3_011_iter_024` (candidate_meta.json / score_report.json / candidate.diff / scheduler_decision.json / verify_stderr.txt)
- keep/reject 원장: `temp/history_docs/branch_history/HISTORY.phase3-family-lineage.md` (phase3_011 절, L2121~)
- 하네스 맥락: `docs/HARNESS-EVOLUTION.md` Gen6 절
