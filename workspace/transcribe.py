"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot — dual acoustic front-end with score-channel arbitration.

Every prior iteration treats the WhisperProcessor's mel extraction as a single
fixed front-end and spends all its machinery *downstream* of it: arbitrating
between hypotheses of one decode (beam N-best, MBR medoid, lexicon rerank,
temperature fallback) or reshaping the search (beam/patience/penalty/prompt).
The one acoustic-input iteration (iter_029) applied a *global, fixed*
pre-emphasis that replaced the whole decode loop and regressed overall, because
it also high-passes the clean windows that did not need restoration.

No iteration has ever run the encoder on **two acoustic views of the same
window and let the decoder's own confidence pick the front-end per window**.
That is the mechanism here. The score channel (``res.scores[0]``, the
length-normalised avg log-prob, requested via ``return_scores=True``) measures
how *explainable* a window's audio is to the decoder. Used in a new role — not
to rank hypotheses of one decode, but to choose between two front-ends:

  - decode the raw window (beam search, T=0). If its avg log-prob clears a
    trust floor, the audio is already clean to the model — accept it, no second
    pass (so confident windows stay single-cost);
  - otherwise the window is band-degraded (the phone-channel obstruent loss
    where the 57 % substitution axis concentrates). Re-encode the *same* window
    after a first-order pre-emphasis ``y[n] = x[n] - alpha*x[n-1]`` — a
    +6 dB/octave high-shelf that restores the fricative/stop-release cues the
    300-3400 Hz telephony band-pass attenuated — and keep whichever front-end
    the decoder finds more explainable (higher avg log-prob).

This attacks substitution exactly where it lives (low-confidence, phone-band
windows) while leaving the clean windows on the front-end that already works.
It is structurally distinct from every ledger entry: the arbitration is across
acoustic FRONT-ENDS, not across hypotheses of one decode, and the spectral
shaping is *selective* (gated on confidence) rather than global like iter_029.

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

# First-order pre-emphasis coefficient (classical +6 dB/octave high-shelf).
# Restores the high-frequency spectral tilt the telephony band-pass removes,
# applied to the raw waveform before the frozen mel extraction.
_PRE_EMPHASIS_ALPHA = 0.97

# avg-logprob trust floor on the raw front-end. At/above this the raw decode is
# already confident, so the second (pre-emphasis) front-end is skipped — keeping
# confident windows single-cost and bounding the dual-encode cost to the
# band-degraded minority where the substitution axis concentrates. Below it the
# window is a candidate for spectral restoration and both front-ends compete.
_RAW_TRUST_LOGPROB = -0.4


def _pre_emphasis(x: np.ndarray, alpha: float) -> np.ndarray:
    out = np.empty_like(x)
    if x.shape[0] == 0:
        return out
    out[0] = x[0]
    out[1:] = x[1:] - alpha * x[:-1]
    return out


def _decode(processor, features_chunk, sr, sot_tokens):
    inputs = processor(
        [features_chunk],
        sampling_rate=sr,
        return_tensors="np",
    )
    features = to_storage_view(inputs.input_features)
    res = generate(
        features,
        [sot_tokens],
        beam_size=_BEAM_SIZE,
        sampling_temperature=0.0,
        return_scores=True,
    )[0]
    score = res.scores[0] if res.scores else float("-inf")
    return res, score


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    n_samples = audio.shape[0]
    win_samples = int(_WINDOW_SECONDS * sr)

    pre_audio = _pre_emphasis(audio, _PRE_EMPHASIS_ALPHA)

    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")
    sot_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )

    pieces: list[str] = []
    cursor = 0
    while cursor < n_samples:
        raw_chunk = audio[cursor : cursor + win_samples]
        chunk_seconds = raw_chunk.shape[0] / sr

        # Front-end 1: raw window. If the decoder is already confident, this view
        # is clean enough and the second encode is skipped.
        chosen, raw_score = _decode(processor, raw_chunk, sr, sot_tokens)

        # Front-end 2: pre-emphasised same window — only when the raw view was
        # low-confidence (band-degraded). Keep whichever front-end the decoder
        # finds more explainable.
        if raw_score < _RAW_TRUST_LOGPROB:
            pre_chunk = pre_audio[cursor : cursor + win_samples]
            pre_res, pre_score = _decode(processor, pre_chunk, sr, sot_tokens)
            if pre_score > raw_score:
                chosen = pre_res

        token_ids = chosen.sequences_ids[0]
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
