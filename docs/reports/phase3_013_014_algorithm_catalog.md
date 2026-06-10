# phase3_013 / phase3_014 Algorithm Catalog

목적: `phase3_013`, `phase3_014`에서 나온 `workspace/transcribe.py` 후보들을
알고리즘 계열별로 묶어, 다른 환경에서 재시험할 때 어떤 방법을 우선 이식할지
판단하기 위한 카탈로그다.

근거 파일:
- `runs/_archive/phase3_013_iter_*/candidate_meta.json`
- `runs/_archive/phase3_013_iter_*/score_report.json`
- `runs/_archive/phase3_014_iter_*/candidate_meta.json`
- `runs/_archive/phase3_014_iter_*/score_report.json`
- 각 후보의 실제 코드 차이는 같은 디렉터리의 `candidate.diff`

주의: 아래 수치는 0715 eval batch의 `corpus_cer` 기준이다. holdout 일반화는 별도
검증이 필요하다.

## 1. Best Runs

| job | best hyp | CER | 핵심 아이디어 |
|---|---:|---:|---|
| `phase3_013` | `phase3_013_iter_022` | `0.153887` | `detect_language()`로 window별 language token 선택 |
| `phase3_014` | `phase3_014_iter_046` | `0.158540` | glossary 조건부 decode를 logprob margin 안에서 채택 |

`phase3_013`가 전체 최저다. `phase3_014`는 다른 계열의 도메인 glossary/조건부
decode 실험으로, 독립적으로 재시험할 가치가 있다.

## 2. Algorithm Groups

### A. Long-form Segmentation / Seek

목표: 30초 고정창으로 생기는 절단, 누락, 반복을 줄인다.

대표 후보:
- `phase3_013_iter_004` (`0.286592`): timestamp 기반 seek advance.
- `phase3_013_iter_006` (`0.175095`): RMS silence floor로 window 끝을 조용한 지점에 맞춤.
- `phase3_013_iter_013` (`0.155107`): silence-cut/final chunk는 `<|notimestamps|>` clean-text mode 사용.
- `phase3_014_iter_004` (`0.170373`): timestamp-guided sequential long-form.

판단:
- timestamp seek는 long-form 전사의 필수 기반에 가깝다.
- `phase3_013_iter_013`의 “seek가 필요 없는 window는 notimestamps”가 15%대 진입에 크게 기여했다.
- heavy overlap, edge defer, VAD-only segmentation 계열은 대체로 불안정했다.

재시험 우선순위: 높음. 단독 알고리즘이라기보다 모든 상위 후보의 backbone으로 본다.

### B. Confidence-gated Fallback / Degeneracy Gate

목표: 낮은 logprob, gzip compression ratio, no-speech signal로 나쁜 decode만 재시도한다.

대표 후보:
- `phase3_013_iter_009` (`0.170861`): gzip compression ratio를 fallback trigger로 추가.
- `phase3_014_iter_006` (`0.166992`): logprob + compression + no_speech 기반 temperature fallback.
- `phase3_014_iter_008` (`0.165833`): temperature ladder 확장.

판단:
- 단독으로 15%대까지는 부족하지만, beam/search/glossary 계열의 안정장치로 유용하다.
- ladder를 과하게 늘리면 runtime과 hallucination 위험이 커진다.

재시험 우선순위: 중간-높음. `beam + timestamp seek` backbone에 붙여 ablation 권장.

### C. Beam / N-best / Search Expansion

목표: greedy가 놓치는 substitution 후보를 beam/N-best로 살린다.

대표 후보:
- `phase3_013_iter_011` (`0.158801`): `beam_size=5`, `num_hypotheses=5`, non-degenerate best 선택.
- `phase3_013_iter_012` (`0.156789`): beam patience 증가.
- `phase3_013_iter_020` (`0.155586`): beam 5 -> 8 확장.
- `phase3_014_iter_014` (`0.159141`): deterministic first pass beam 강화.
- `phase3_014_iter_019` (`0.159699`): beam 8 변형.

판단:
- beam 5 + gated selector는 강한 기본기다.
- beam을 너무 키우는 것은 일관되게 큰 개선을 만들지는 못했다.
- MBR/ROVER/majority vote 계열은 아이디어는 다양했지만 상위권을 넘지 못했다.

재시험 우선순위: 높음. `beam_size=5`, `num_hypotheses=5`, `patience` 소폭 조정부터.

### D. Decoder Context / Glossary / Domain Prior

목표: 보험 도메인 용어 substitution을 줄인다.

대표 후보:
- `phase3_013_iter_010` (`0.165476`): confident previous window text를 `<|startofprev|>`로 carry.
- `phase3_014_iter_012` (`0.167053`): static domain glossary prime.
- `phase3_014_iter_039` (`0.159037`): glossary-primed condition B를 별도 sequential decode로 실행.
- `phase3_014_iter_046` (`0.158540`): condition B logprob가 A보다 조금 낮아도 margin 안이면 채택.
- `phase3_014_iter_051` (`0.158671`), `phase3_014_iter_076` (`0.158801`): glossary trigger 확장.

판단:
- glossary를 무조건 주입하면 drift가 생길 수 있다.
- 가장 좋은 형태는 “기본 decode A + glossary decode B를 만들고, B가 A에 근접할 때만 채택”이다.
- `phase3_014_iter_046`은 별도 계열로 재시험할 가치가 높다.

재시험 우선순위: 높음. 특히 substitution-heavy 데이터셋에서 우선 테스트.

### E. Alignment / Acoustic Posterior

목표: decoder score가 아니라 acoustic alignment로 loop tail, weak token, 후보 선택을 보정한다.

대표 후보:
- `phase3_013_iter_014` (`0.155185`): degenerate window에 `align()` 후 posterior-floor trim.
- `phase3_013_iter_016` (`0.154828`): align trim trigger를 `degenerate or low-logprob`로 확장.
- `phase3_013_iter_018` (`0.155107`): alignment frame advance 기반 trailing run trim.
- `phase3_013_iter_021` (`0.156057`): top beams를 align posterior로 rerank.
- `phase3_014_iter_023` (`0.161886`): low-margin fork에서 align posterior selector.

판단:
- `align()`은 “검증/trim”으로 쓸 때 강했다.
- boundary seek 자체를 align frame으로 대체한 변형은 큰 회귀가 있었다.
- acoustic posterior는 강하지만 비싸고, trigger를 좁혀야 한다.

재시험 우선순위: 높음. `phase3_013_iter_016` 계열을 별도 candidate로 재현 권장.

### F. Language Detection / Code-switch Handling

목표: 모든 window를 강제로 `<|ko|>`로 두는 것에서 생기는 영어/외국어 용어의 한글 음차 substitution을 줄인다.

대표 후보:
- `phase3_013_iter_022` (`0.153887`): `model.detect_language(features)` top token이 non-Korean이고 confidence >= 0.7이면 language token 교체.
- `phase3_013_iter_023` (`0.154541`): ambiguous band에서 Korean/non-Korean dual decode 후 선택.
- `phase3_014_iter_030` (`0.161799`): Korean posterior gate + preemphasis rescue.

판단:
- 전체 최저 성능은 이 계열에서 나왔다.
- 단순한 confident override가 dual decode보다 나았다.
- 구현 시 `detect_language()` 추가 encoder pass 비용을 runtime budget 안에서 확인해야 한다.

재시험 우선순위: 최상. 첫 번째로 이식할 알고리즘.

### G. Repetition / Hallucination Suppression

목표: 반복 문구, YouTube-style hallucination, seam duplicate를 줄인다.

대표 후보:
- `phase3_013_iter_008` (`0.189969`): `no_repeat_ngram_size=3`.
- `phase3_013_iter_024` (`0.158409`): beam pass에 repetition penalty.
- `phase3_014_iter_048` (`0.158976`): no_speech_prob hallucination guard 조합.
- `phase3_014_iter_085` (`0.159403`): consecutive segment seam dedup.

판단:
- repetition penalty류는 단독으로는 substitution을 잘 해결하지 못한다.
- hallucination/repeat guard는 상위 후보의 안전장치로는 의미가 있다.
- 너무 강한 repeat ban은 정상 반복 표현까지 손상할 수 있다.

재시험 우선순위: 중간. best 후보에 guard로 붙여 확인.

### H. Output Post-correction / Lexical Rewrite

목표: decode 후 jamo/글자 패턴으로 도메인 용어 오자를 보정한다.

대표 후보:
- `phase3_014_iter_041` (`0.160030`): jamo decomposition + glossary correction.
- `phase3_014_iter_044` (`0.161773`): align-verified lexicon rewrite.
- `phase3_014_iter_067` (`0.160309`): self-consensus singleton canonicalization.

판단:
- 16% 근처까지는 오지만 15%대 best를 넘지는 못했다.
- label/domain overfit 위험이 커서 holdout 검증 없이는 위험하다.

재시험 우선순위: 낮음-중간. 도메인 용어 사전이 안정적인 경우에만.

### I. Audio / Feature Frontend

목표: 8 kHz 전화 음성의 consonant-band, spectral tilt, dynamic range 문제를 입력에서 보정한다.

대표 후보:
- `phase3_013_iter_026` (`0.158575`): waveform high-pass.
- `phase3_014_iter_013` (`0.170747`): pre-emphasis.
- `phase3_014_iter_031` (`0.177561`): mel high-frequency lift.
- `phase3_014_iter_063` (`0.165598`): per-bin variance equalization.

판단:
- 일부는 15%대 근처까지 갔지만 일관성은 낮았다.
- Whisper feature 분포를 건드리면 회귀가 쉽게 난다.

재시험 우선순위: 낮음. 다른 계열이 포화된 뒤 ablation.

### J. Ensemble / Cross-condition / Dual-grid

목표: 서로 다른 segmentation, warped feature, sampling draw, phase-shift decode를 witness로 써서 교정한다.

대표 후보:
- `phase3_014_iter_026` (`0.167410`): dual phase segmentation reconcile.
- `phase3_014_iter_033` (`0.164744`): raw + CMN alternative align select.
- `phase3_014_iter_068` (`0.161860`): phase-shift witness near seam.
- `phase3_014_iter_074` (`0.159716`): length penalty combine.
- `phase3_014_iter_078` (`0.160230`): notimestamps retranscribe compose.

판단:
- compute가 커지고 복잡도 대비 이득이 작았다.
- seam 주변 보조 witness로 제한하면 가능성이 있다.

재시험 우선순위: 낮음-중간. 단순 best 재현 뒤 실험.

## 3. Recommended Test Order

다른 환경에서 재시험할 때는 한 번에 합치지 말고 아래 순서로 독립 candidate를 만든다.

1. **F: detect-language override**  
   기준 후보: `phase3_013_iter_022`, CER `0.153887`. 전체 최저.

2. **E: align low-logprob trim**  
   기준 후보: `phase3_013_iter_016`, CER `0.154828`. 반복/loop tail에 강함.

3. **D: glossary condition B + margin**  
   기준 후보: `phase3_014_iter_046`, CER `0.158540`. 도메인 substitution 보정.

4. **C: beam5 + N-best selector + patience**  
   기준 후보: `phase3_013_iter_011` / `012`, CER `0.158801` / `0.156789`.

5. **A: notimestamps clean-text mode for non-seek windows**  
   기준 후보: `phase3_013_iter_013`, CER `0.155107`. backbone 개선.

6. **B/G: fallback + hallucination/repetition guard**  
   상위 후보에 안전장치로만 붙여 ablation.

## 4. Avoid / Low Priority

아래 계열은 같은 데이터에서 자주 회귀했다.

- heavy overlap / edge-defer / sliding window stitching
- VAD-only segmentation
- align frame을 seek boundary 자체로 사용하는 방식
- feature-domain CMN/warping/equalization을 강하게 적용하는 방식
- hard lexical rewrite를 넓게 적용하는 방식
- MBR/ROVER/majority vote를 전체 hypothesis 선택에 쓰는 방식

## 5. Reproduction Pointers

각 알고리즘의 실제 patch는 다음 파일에서 바로 확인할 수 있다.

- `runs/_archive/phase3_013_iter_022/candidate.diff`
- `runs/_archive/phase3_013_iter_016/candidate.diff`
- `runs/_archive/phase3_013_iter_013/candidate.diff`
- `runs/_archive/phase3_013_iter_011/candidate.diff`
- `runs/_archive/phase3_013_iter_012/candidate.diff`
- `runs/_archive/phase3_014_iter_046/candidate.diff`
- `runs/_archive/phase3_014_iter_039/candidate.diff`

다른 데이터셋에서는 `corpus_cer`뿐 아니라 다음을 같이 본다.

- `sub_ratio`: glossary/language/beam 계열의 주 타깃
- `del_ratio`와 `length_ratio.p05`: seek/windowing 회귀 감지
- `repeated_text_rate`, `hallucination_hit_rate`: fallback/glossary 부작용 감지
- `total_inference_time_s`: `detect_language`, `align`, dual decode의 비용 확인
