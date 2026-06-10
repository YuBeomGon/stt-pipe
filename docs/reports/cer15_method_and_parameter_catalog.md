# CER 15%대 후보 Method / Parameter Catalog

대상: `0.15 <= corpus_cer < 0.16`에 들어온 후보 41개.

복원 코드:
- `temp/cer15_transcribes/<job>/<hyp_id>/transcribe.py`
- `temp/cer15_transcribes/MANIFEST.tsv`

근거:
- 현재 `runs/_archive/phase3_013*`, `runs/_archive/phase3_014*`
- `docs/history-archive/runs/phase3_004_runs_sanitized.tar.gz` 해제본
- 각 후보의 `score_report.json`, `candidate_meta.json`, `candidate.diff`, 복원된 `transcribe.py`

주의: 아래는 0715 eval 기준이다. holdout/다른 corpus에서는 반드시 독립 평가해야 한다.

## 1. 공통 Backbone

15%대 후보 다수는 완전히 다른 알고리즘이 아니라, 아래 backbone 위에 기능/파라미터가
쌓인 형태다.

| 축 | 공통 경향 |
|---|---|
| chunk | 30초 window |
| timestamp | timestamp token을 사용해 seek advance |
| gate | `avg_logprob < -1.0`, compression ratio `> 2.4`를 실패 신호로 사용 |
| fallback | 실패 window만 temperature fallback |
| search | 상위권은 greedy보다 beam/N-best 사용 |
| context | 일부는 `<|startofprev|>`로 이전 context 또는 glossary 주입 |

## 2. 후보별 Method Matrix

### phase3_013

| hyp | CER | 방법 | 주요 파라미터 / 튜닝값 |
|---|---:|---|---|
| `phase3_013_iter_022` | `0.153887` | `detect_language(features)`로 window별 language token 선택. confident non-Korean이면 `<|ko|>` 대신 detected token 사용. | `_LANG_OVERRIDE_PROB=0.7`, `beam=5`, `num_hypotheses=5`, `patience=2.0`, temps `(0.2,0.4,0.6)`, `logprob=-1.0`, compression `2.4`, `no_speech=0.6`, align trim `prob<0.3`, min run `8`. |
| `phase3_013_iter_023` | `0.154541` | language detection 확장. ambiguous band에서는 Korean decode와 detected-language decode를 둘 다 실행하고 선택. | `_LANG_DUAL_PROB=0.4`, `_LANG_OVERRIDE_PROB=0.7`; 그 외 iter_022 계열 backbone. |
| `phase3_013_iter_016` | `0.154828` | `align()` posterior 기반 loop-tail trim의 trigger 확장. gzip-degenerate뿐 아니라 low-logprob window도 align trim. | `align text_token_probs`, `_ALIGN_PROB_FLOOR=0.3`, `_ALIGN_MIN_RUN=8`, trigger: `best_degenerate or best_logprob < -1.0`, `beam=5`, `patience=2.0`. |
| `phase3_013_iter_013` | `0.155107` | seek가 필요 없는 silence-cut/final chunk는 `<|notimestamps|>` clean-text decode. timestamp tax를 줄임. | `beam=5`, `patience=2.0`, temps `(0.2,0.4,0.6)`, timestamp mode는 dense full window에만 유지. |
| `phase3_013_iter_018` | `0.155107` | `align()`의 token-to-frame alignment를 이용해 trailing loop run trim. posterior floor trim과 frame-advance trim 중 더 공격적인 cut 사용. | `_ALIGN_FRAME_STEP` 계열 frame advance gate, `_ALIGN_PROB_FLOOR=0.3`, `_ALIGN_MIN_RUN=8`. |
| `phase3_013_iter_014` | `0.155185` | gzip gate가 degenerate로 본 window에만 `align()` 실행 후 trailing low-posterior token run 제거. | `_ALIGN_PROB_FLOOR=0.3`, `_ALIGN_MIN_RUN=8`; trigger는 `best_degenerate`. |
| `phase3_013_iter_019` | `0.155403` | N-best disagreement를 context carry gate로 사용. beam들이 서로 다르면 해당 window text를 다음 window context로 carry하지 않음. | beam consensus by `difflib`, threshold 약 `0.8`; context cap `200` tokens. |
| `phase3_013_iter_020` | `0.155586` | beam search 폭 확대. N-best selector는 유지하고 beam 후보 pool만 넓힘. | `beam_size=8`, `num_hypotheses=8`, 기존 `patience=2.0`. |
| `phase3_013_iter_015` | `0.155604` | N-best token-level majority vote. best beam을 backbone으로 두고 동률은 best beam 유지. | `beam=5`, `num_hypotheses=5`, per-position majority over non-degenerate beams. |
| `phase3_013_iter_021` | `0.156057` | top N-best 후보를 `align()` acoustic posterior로 rerank. decoder logprob 대신 acoustic grounding 반영. | top `2` distinct beams rerank, align mean posterior 비교. |
| `phase3_013_iter_017` | `0.156762` | decode-time token ban. kana/CJK ideograph token을 `suppress_tokens`로 금지해 cross-script substitution 감소 시도. | `suppress_tokens=[-1, *kana/cjk ids]`; beam/fallback 양쪽 적용. |
| `phase3_013_iter_012` | `0.156789` | beam pruning 완화. 같은 beam width에서 더 오래 후보를 살림. | `patience=2.0` around `beam=5`, `num_hypotheses=5`. |
| `phase3_013_iter_025` | `0.157486` | length normalization으로 짧은 beam 후보를 덜 선호하게 조정. deletion/length underrun 대응. | `length_penalty=1.2` on first-pass beam. |
| `phase3_013_iter_024` | `0.158409` | repetition loop를 decode 단계에서 약하게 억제. | `repetition_penalty=1.1` on first-pass beam only. |
| `phase3_013_iter_026` | `0.158575` | waveform high-pass. 저주파/DC/rumble 제거 후 feature extraction. | moving-average high-pass, window width roughly `sr/80`. |
| `phase3_013_iter_011` | `0.158801` | greedy first pass를 beam/N-best selector로 교체. non-degenerate 우선, logprob tie-break. | `beam_size=5`, `num_hypotheses=5`, `return_scores=True`, `return_no_speech_prob=True`. |

### phase3_014

| hyp | CER | 방법 | 주요 파라미터 / 튜닝값 |
|---|---:|---|---|
| `phase3_014_iter_046` | `0.158540` | glossary condition-B 채택 margin. glossary-primed decode가 A보다 조금 낮아도 margin 내면 채택. | `_GLOSSARY_LOGPROB_MARGIN=0.1`, `beam=5`, `patience=1.0`, temps `(0.0,0.2,0.4,0.6,0.8,1.0)`, `logprob=-1.0`, compression `2.4`. |
| `phase3_014_iter_051` | `0.158671` | glossary condition-B를 더 많은 borderline window에 실행. adoption margin은 유지. | cond-B trigger widened to around `lp_a < -0.5`, adoption margin `0.1`. |
| `phase3_014_iter_076` | `0.158801` | iter_051과 같은 계열. glossary trigger threshold를 acceptance gate와 분리. | `_GLOSSARY_TRIGGER_LOGPROB=-0.5`, acceptance remains `-1.0`, margin `0.1`. |
| `phase3_014_iter_048` | `0.158976` | no-speech posterior로 hallucination guard 추가. beam/glossary confidence-max path의 부작용 방어. | `return_no_speech_prob`; paired threshold guard, glossary/beam parent 조합. |
| `phase3_014_iter_039` | `0.159037` | glossary-primed condition을 batched generate 대신 별도 sequential generate로 실행. gate-failing window에서만 B 실행 후 logprob 높은 쪽 선택. | cond-B only when A fails `logprob/compression` gate; strict `score_B > score_A`. |
| `phase3_014_iter_014` | `0.159141` | temperature fallback backbone의 deterministic first pass를 greedy에서 beam으로 교체. | `beam_size=5`, `patience=1.0`, fallback temps `(0.0..1.0)`. |
| `phase3_014_iter_050` | `0.159211` | temperature ladder trim. high-temp rung이 noise를 만든다고 보고 상한 축소. | attempted trim to `(0.0,0.2,0.4,0.6)` 계열. |
| `phase3_014_iter_085` | `0.159403` | seam duplicate 제거. 직전 emitted segment와 완전히 같은 window text를 suppress. | exact consecutive segment dedup; crash난 align-pool 시도는 revert. |
| `phase3_014_iter_019` | `0.159699` | first-pass beam 폭 확대. | `beam_size=8`, `patience=1.0`. |
| `phase3_014_iter_074` | `0.159716` | length penalty를 cond-A/cond-B 양쪽 beam에 적용. glossary substitution 보정에 deletion 보정 추가. | `length_penalty=1.2` on both beam passes, glossary margin `0.1`. |

### phase3_004

| hyp | CER | 방법 | 주요 파라미터 / 튜닝값 |
|---|---:|---|---|
| `phase3_004_iter_018` | `0.157355` | faster-whisper식 conditional fallback 조립. low confidence / high compression window만 temperature sample로 재시도. | `beam=5` first pass, temps `(0.0,0.4,0.8)`, `logprob=-1.0`, compression `2.4`, `_MIN_ADVANCE_SECONDS=2.0`. |
| `phase3_004_iter_049` | `0.157747` | per-window `detect_language()`를 prompt language slot에 적용. | language prob floor around `0.5`, 기존 fallback/seek backbone. |
| `phase3_004_iter_027` | `0.157843` | leading blank 억제 해제. cold onset에서 첫 content token 강제 commit을 줄임. | `suppress_blank=False`. |
| `phase3_004_iter_037` | `0.157886` | no-speech 높은 window에만 align trim. trailing hallucination/low-acoustic tail 제거. | `no_speech_prob > 0.5`, align prob floor `0.2`, temps `(0.0,0.4,0.8)`. |
| `phase3_004_iter_020` | `0.158008` | beam patience 증가로 early EOT/short beam 완화. | `patience=2.0` on temp-0 beam pass. |
| `phase3_004_iter_022` | `0.158078` | per-window language token 선택. 013의 best와 같은 계열의 초기 버전. | `detect_language()` result used directly for language token. |
| `phase3_004_iter_050` | `0.158200` | 첫 timestamp를 0초로 강제해 leading deletion 방지 시도. | `max_initial_timestamp_index=0` on beam and fallback paths. |
| `phase3_004_iter_031` | `0.158296` | gate-failing window에서 diverse samples의 medoid 선택. score 대신 hypothesis agreement 사용. | `_CONSENSUS_N=5` 계열, `SequenceMatcher` mean similarity. |
| `phase3_004_iter_033` | `0.158305` | ROVER/token-majority vote. medoid backbone에 sample token majority를 합성. | multiple sampled hypotheses, majority vote, ties keep backbone. |
| `phase3_004_iter_019` | `0.158435` | fallback sample을 하나가 아니라 여러 개 뽑아 best score 선택. | `_BEST_OF=5`, sampling path `num_hypotheses=5`. |
| `phase3_004_iter_025` | `0.158828` | 기본 non-speech suppression 해제. token availability를 늘려 under-emission 완화 시도. | `suppress_tokens=[]` instead of default `[-1]`. |
| `phase3_004_iter_023` | `0.158967` | N-best 중 score margin 안의 가장 긴 beam 선택. deletion/length ratio 보정. | longest beam within score margin `0.15`, compression gate 유지. |
| `phase3_004_iter_036` | `0.159080` | longest beam을 align acoustic gate로 검증 후 채택. | keep longer hypothesis if align mean prob within `top - 0.08`. |
| `phase3_004_iter_029` | `0.159803` | dropped trailing segment text를 `<|startofprev|>`로 다음 window에 carry. | carry only trailing segment, not full transcript. |
| `phase3_004_iter_021` | `0.159978` | running transcript context carry + confidence gate reset. | context cap around `200` tokens; reset when low confidence/compression gate trips. |

## 3. Parameter Axes

### Beam / Search

| 파라미터 | 값 | 사용 후보 | 관찰 |
|---|---|---|---|
| `beam_size` | `5` | `013_iter_011`, `013_iter_016`, `014_iter_014`, `014_iter_046`, `004_iter_018` 등 | 15%대 backbone의 핵심값. |
| `beam_size` | `8` | `013_iter_020`, `014_iter_019` | 개선은 있었지만 best는 아님. 비용 증가. |
| `num_hypotheses` | `beam_size` 또는 `5` | `013_iter_011`, `013_iter_015`, `004_iter_019`, `004_iter_031`, `004_iter_033` | selector/consensus/majority 실험의 기반. |
| `patience` | `2.0` | `013_iter_012` 이후 다수, `004_iter_020` | 013 계열에서는 강함. 014 계열은 `1.0` 유지 후보도 많음. |
| `length_penalty` | `1.2` | `013_iter_025`, `014_iter_074` | deletion/short hypothesis 대응. 단독 best는 아님. |

### Acceptance / Fallback Gates

| 파라미터 | 값 | 사용 후보 | 관찰 |
|---|---|---|---|
| avg logprob gate | `-1.0` | 거의 모든 fallback 계열 | 표준 confidence gate로 계속 유지됨. |
| compression ratio | `2.4` | fallback/degeneracy 계열 | repetitive decode 감지에 유효. |
| no speech gate | `0.6` 또는 `>0.5` | `013` 계열, `004_iter_037`, `014_iter_048` | hallucination guard로 의미 있음. |
| temperature ladder | `(0.0,0.4,0.8)` | `004` 계열 | 단순하고 비용 낮음. |
| temperature ladder | `(0.2,0.4,0.6)` | `013` 상위 계열 | beam first pass 뒤 fallback만 sampling. |
| temperature ladder | `(0.0,0.2,0.4,0.6,0.8,1.0)` | `014` glossary 계열 | 넓지만 high-temp 부작용 가능. |

### Language / Code-switch

| 파라미터 | 값 | 사용 후보 | 관찰 |
|---|---|---|---|
| language override prob | `0.7` | `013_iter_022`, `013_iter_023` | 전체 최저. confident non-Korean only가 안전. |
| dual decode band | `0.4..0.7` | `013_iter_023` | 022보다 약간 나쁨. 비용 증가 대비 이득 제한. |
| language prob floor | `0.5` 근처 | `004_iter_049` | 초기 버전도 15%대. |

### Alignment

| 파라미터 | 값 | 사용 후보 | 관찰 |
|---|---|---|---|
| align prob floor | `0.3` | `013_iter_014`, `013_iter_016`, `013_iter_018` | loop-tail trim에 강함. |
| align min run | `8` tokens | `013_iter_014+` | 과도한 clipping 방지. |
| align trigger | degenerate only | `013_iter_014` | 좋은 출발점. |
| align trigger | degenerate or low-logprob | `013_iter_016` | 더 좋음. |
| no-speech align trigger | `no_speech_prob > 0.5` | `004_iter_037` | hallucination tail trim 계열. |

### Glossary / Domain Prior

| 파라미터 | 값 | 사용 후보 | 관찰 |
|---|---|---|---|
| cond-B strict adopt | `score_B > score_A` | `014_iter_039` | 0.159대. |
| cond-B margin | `score_B >= score_A - 0.1` | `014_iter_046` | 014 best. |
| cond-B trigger | gate-fail only | `014_iter_039`, `014_iter_046` | 안전하지만 reach 제한. |
| cond-B trigger | `lp_a < -0.5` | `014_iter_051`, `014_iter_076` | reach 확대, best보다 약간 나쁨. |

### Suppression / Repetition

| 파라미터 | 값 | 사용 후보 | 관찰 |
|---|---|---|---|
| `repetition_penalty` | `1.1` | `013_iter_024` | loop 완화, best는 아님. |
| `no_repeat_ngram_size` | `3` 계열 | 15% 밖 후보 및 관련 lineage | 강하면 정상 반복까지 손상 가능. |
| `suppress_tokens` | kana/CJK ban | `013_iter_017` | cross-script substitution에 제한적 유효. |
| `suppress_tokens` | `[]` | `004_iter_025` | default non-speech suppression 해제 실험. |
| `suppress_blank` | `False` | `004_iter_027` | cold-onset substitution에 유효 가능. |

## 4. Method Families

### 4.1 Best-first 후보

1. `phase3_013_iter_022`: language override + strong 013 backbone.
2. `phase3_013_iter_016`: align low-logprob trim + beam/patience backbone.
3. `phase3_004_iter_018`: conditional temperature fallback.
4. `phase3_014_iter_046`: glossary condition-B margin.

### 4.2 같은 방식의 파라미터 튜닝으로 묶이는 후보

| family | 후보 | 튜닝 차이 |
|---|---|---|
| language detection | `004_iter_022`, `004_iter_049`, `013_iter_022`, `013_iter_023` | direct language token, prob floor, `0.7` confident override, `0.4..0.7` dual decode. |
| align trim | `013_iter_014`, `013_iter_016`, `013_iter_018`, `004_iter_037` | trigger, posterior floor, min run, frame advance, no-speech trigger. |
| beam search | `013_iter_011`, `013_iter_012`, `013_iter_020`, `014_iter_014`, `014_iter_019` | `beam=5/8`, `patience=1/2`, N-best availability. |
| N-best selection | `013_iter_015`, `013_iter_019`, `013_iter_021`, `004_iter_031`, `004_iter_033`, `004_iter_036` | majority vote, context gate, acoustic rerank, medoid, ROVER, align-gated longest. |
| fallback ladder | `004_iter_018`, `004_iter_019`, `014_iter_050` | temp list, best-of samples, high-temp trim. |
| glossary | `014_iter_039`, `014_iter_046`, `014_iter_051`, `014_iter_076` | strict vs margin adoption, trigger threshold. |
| length/deletion | `004_iter_020`, `004_iter_023`, `004_iter_050`, `013_iter_025`, `014_iter_074` | patience, longest within margin, timestamp start clamp, length penalty. |
| suppression | `013_iter_017`, `013_iter_024`, `004_iter_025`, `004_iter_027`, `014_iter_085` | script token ban, repetition penalty, default suppression off, suppress blank off, seam dedup. |

## 5. Suggested Re-test Matrix

다른 곳에서 테스트할 때는 아래처럼 한 축씩 켜는 것이 좋다.

| step | candidate base | change | 이유 |
|---|---|---|---|
| 1 | simple timestamp-seek + fallback | `beam=5`, `num_hypotheses=5`, `patience=2.0` | 013 계열 안정 backbone. |
| 2 | step 1 | `detect_language` confident override `>=0.7` | 전체 best의 핵심. |
| 3 | step 1 | align trim `prob<0.3`, min run `8`, trigger low-logprob 포함 | second-best 계열. |
| 4 | step 1 | conditional fallback temps `(0.0,0.4,0.8)` or `(0.2,0.4,0.6)` | low-confidence rescue 비교. |
| 5 | step 1 | glossary condition-B, margin `0.1` | 도메인 substitution dataset에서 확인. |
| 6 | best of 2-5 | length penalty `1.2`, `max_initial_timestamp_index=0`, `suppress_blank=False` 각각 ablation | deletion/cold-onset 개선 여부 확인. |

한 번에 모두 합치면 원인 attribution이 어렵다. 특히 `detect_language`, `align`, glossary
condition-B는 모두 substitution을 건드리므로 독립 ablation 후 결합해야 한다.
