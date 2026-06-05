"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot — lexicon-guided N-best rescoring. Every prior consensus/selection
iteration that consumed the N-best return channel (the beam-vote iters 011/024-
028 and the independent-sample MBR/medoid iters 051-056) selected among the
hypotheses by **geometry**: medoid by edit-similarity, position-wise word vote,
confidence-weighted Bayes risk — all measures of *central tendency of the
cloud*. None of them ever consulted **external domain knowledge** about which
spelling is correct. Geometry cannot help when a phone-band obstruent
substitution is *confident*: the ledger established (iter_009/011/024) that
beams are >=95% correlated on their errors, so a confidently-wrong domain term
sits identically in every hypothesis and the medoid inherits it. Central
tendency has no opinion about whether 보험 or 보훔 is a real word.

The dominant axis is substitution (57%, length_ratio 0.96 healthy): the headroom
is in *what* gets mis-recognised — insurance call-center domain terms degraded by
the 300-3400 Hz telephony band-pass. This iteration introduces a fundamentally
different selection criterion over the same single-pass beam N-best: rerank the
beam_size hypotheses by

    combined = avg_logprob(res.scores[i]) + LAMBDA * lexicon_hits(text_i)

where lexicon_hits counts occurrences of a fixed Korean insurance/call-center
lexicon in each hypothesis. The decoder's own beam ranking (raw log-prob) often
ranks a phonetically-adjacent non-word at parity with the correct domain term
because the high-frequency cues that separate them were filtered out; a single
hit of *external lexical knowledge* breaks that tie toward the in-domain
spelling. This is single-pass (one beam generate per window, num_hypotheses=
beam_size, return_scores=True), so the encoder cost is the incumbent's and the
runtime stays in budget — unlike the temperature-fallback / two-pass machinery.

Distinct from iter_004's <|startofprev|> glossary *prompt* biasing: that pushes
the prior into the decoder *before* search (and competes for the 224 prompt
positions); this leaves the search untouched and applies the lexicon as a
*post-hoc rescorer* over completed hypotheses, so it can never crowd out the
audio decode and never propagate a wrong prompt token.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

_WINDOW_SECONDS = 30.0
_TIME_PRECISION = 0.02
_MIN_ADVANCE_SECONDS = 2.0

_BEAM_SIZE = 5
# Take the full beam N-best so the rescorer has hypotheses to choose among.
_NUM_HYPOTHESES = 5

# Weight on a single in-lexicon match, in avg-logprob units. avg_logprob on
# phone-band Korean windows sits in roughly [-1.0, -0.2], so LAMBDA=0.15 lets a
# domain-term match override a beam-rank inversion of comparable magnitude
# without letting term-count alone dominate the acoustic likelihood.
_LEXICON_LAMBDA = 0.15

# Fixed Korean insurance / call-center domain lexicon. These are exactly the
# substitution-prone terms whose distinguishing high-frequency consonant cues
# the telephony band-pass attenuates, so the decoder's raw log-prob ranks the
# correct spelling at parity with a phonetic neighbour. Counting their presence
# in each hypothesis is the external knowledge the geometric selectors lacked.
# REFINE on iter_063: score by DISTINCT terms present, not total occurrences —
# multiplicity rewarded a hypothesis that loops one domain term ("보험 보험
# 보험"), the structural source of iter_063's insertion regression
# (ins 0.08 best → 0.12). Presence keeps the rerank a pure in-domain tiebreaker.
_LEXICON = (
    "보험", "보험료", "보험금", "계약", "보장", "가입", "가입자", "피보험자",
    "수익자", "청구", "약관", "해지", "환급", "갱신", "특약", "만기", "납입",
    "자동이체", "고객님", "상담", "본인", "확인", "동의", "안내", "명의",
    "통장", "카드", "연락처", "주민등록번호", "사고", "접수", "지급", "심사",
)


def _lexicon_hits(text: str) -> int:
    if not text:
        return 0
    return sum(1 for term in _LEXICON if term in text)


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    n_samples = audio.shape[0]
    win_samples = int(_WINDOW_SECONDS * sr)

    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")
    sot_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )

    pieces: list[str] = []
    cursor = 0
    while cursor < n_samples:
        chunk = audio[cursor : cursor + win_samples]
        chunk_seconds = chunk.shape[0] / sr

        inputs = processor(
            [chunk],
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        res = generate(
            features,
            [sot_tokens],
            beam_size=_BEAM_SIZE,
            num_hypotheses=_NUM_HYPOTHESES,
            sampling_temperature=0.0,
            return_scores=True,
        )[0]

        # Lexicon-guided rescoring over the N-best. Decode each hypothesis to
        # text, score it as avg_logprob + LAMBDA * lexicon_hits, and pick the
        # argmax. Falls back to beam[0] when no hypothesis contains a domain
        # term (the rescorer is then inert and the decoder's own ranking wins).
        best_idx = 0
        best_combined = float("-inf")
        for i, token_ids in enumerate(res.sequences_ids):
            text_tokens = [t for t in token_ids if t < timestamp_begin]
            text_i = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
            avg_logprob = res.scores[i] if res.scores else 0.0
            combined = avg_logprob + _LEXICON_LAMBDA * _lexicon_hits(text_i)
            if combined > best_combined:
                best_combined = combined
                best_idx = i

        token_ids = res.sequences_ids[best_idx]
        text_tokens = [t for t in token_ids if t < timestamp_begin]
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]

        text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
