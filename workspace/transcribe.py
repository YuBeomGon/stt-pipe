"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prompt-conditioning iteration on this job fed the decoder
only PAST context: iters 071-075 carried the immediately-previous window's text
through the ``<|startofprev|>`` prior (causal), and iters 099-102 carried a
document-level frequency Counter (order-free but still aggregated from windows
already decoded). No iteration has ever conditioned a window on the text of the
window that comes *after* it — the decoder has never seen right-context.

This slot introduces a fundamentally different pipeline: a **two-pass,
bidirectionally-conditioned decode**.

  - Pass 1 decodes every window causally (no prior context) at T=0 beam search,
    recording each window's text, its acoustic boundaries, and — read off the
    score channel (``return_scores=True``) — its length-normalised avg log-prob.
  - Pass 2 walks the same windows again, but for each window it builds the
    ``<|startofprev|>`` prior from BOTH neighbours: the tail of the previous
    window's pass-1 text AND the head of the *next* window's pass-1 text. The
    decoder now sees how the utterance continues, not just where it came from.

Why this attacks the dominant 57% substitution axis: a domain term whose
high-frequency obstruent cues are attenuated on the 300-3400 Hz phone band is
acoustically ambiguous against a phonetic neighbour. Left context alone (the
whole prompt lineage so far) often cannot break the tie. The *continuation* —
the grammatical/semantic right-context that the next window already transcribed
— frequently can: the neighbour spelling leaves a continuation the next window
does not support, so the bidirectional prior shifts posterior mass onto the
correct completion. This is a disambiguation signal no prior iter had access to.

The second pass is the expensive half (a re-encode + re-decode per window), so
it is **gated on pass-1 avg log-prob**: only windows the score channel flags as
low-confidence — exactly where phone-band substitution concentrates — are
re-decoded with the bidirectional prior; confident windows keep their pass-1
text untouched. That bounds the extra compute to the uncertain minority and
keeps runtime inside the baseline budget.

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

# Number of neighbour BPE tokens fed into the <|startofprev|> bidirectional
# prior: the tail of the previous window and the head of the next window. Capped
# so the carried span stays a soft prior and does not become a re-emission
# reservoir (iter_073's finding on long carried spans).
_CTX_PREV_TOKENS = 32
_CTX_NEXT_TOKENS = 32

# Pass-2 gate. A window whose pass-1 length-normalised avg log-prob is below this
# floor is acoustically uncertain — where the 57% substitution axis lives — and
# is worth the bidirectional re-decode. Confident windows keep their pass-1 text,
# bounding the second pass to the uncertain minority so runtime stays in budget.
_RESCUE_LOGPROB = -0.6


def _decode(features, prompt, tokenizer, timestamp_begin):
    res = generate(
        features,
        [prompt],
        beam_size=_BEAM_SIZE,
        sampling_temperature=0.0,
        return_scores=True,
    )[0]
    avg_logprob = res.scores[0] if res.scores else float("-inf")
    token_ids = res.sequences_ids[0]
    text_tokens = [t for t in token_ids if t < timestamp_begin]
    ts_tokens = [t for t in token_ids if t >= timestamp_begin]
    text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
    return text, ts_tokens, avg_logprob


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
    startofprev = tokenizer.convert_tokens_to_ids("<|startofprev|>")

    # ---- Pass 1: causal decode; record features, text, avg log-prob ----
    windows = []  # (features, text, avg_logprob)
    cursor = 0
    while cursor < n_samples:
        chunk = audio[cursor : cursor + win_samples]
        chunk_seconds = chunk.shape[0] / sr

        inputs = processor([chunk], sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        text, ts_tokens, avg_logprob = _decode(
            features, sot_tokens, tokenizer, timestamp_begin
        )
        windows.append((features, text, avg_logprob))

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)
        cursor += max(1, int(advance_seconds * sr))

    # ---- Pass 2: bidirectional-context re-decode of low-confidence windows ----
    pieces: list[str] = []
    for i, (features, p1_text, avg_logprob) in enumerate(windows):
        text = p1_text
        if avg_logprob < _RESCUE_LOGPROB:
            ctx_ids: list[int] = []
            if i > 0:
                prev_ids = tokenizer.encode(windows[i - 1][1], add_special_tokens=False)
                ctx_ids += prev_ids[-_CTX_PREV_TOKENS:]
            if i + 1 < len(windows):
                next_ids = tokenizer.encode(windows[i + 1][1], add_special_tokens=False)
                ctx_ids += next_ids[:_CTX_NEXT_TOKENS]

            if ctx_ids:
                prompt = [startofprev] + ctx_ids + sot_tokens
                text, _, _ = _decode(features, prompt, tokenizer, timestamp_begin)

        if text:
            pieces.append(text)

    return " ".join(pieces)
