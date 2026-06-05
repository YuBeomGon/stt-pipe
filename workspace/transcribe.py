"""Workspace transcribe — autoresearch evolves this file.

Timestamp-driven sequential long-form decoding. Every prior iteration in this
job decoded with ``<|notimestamps|>`` in the SOT prefix and then imposed chunk
boundaries *externally* — first blind 30s strides (iter_001), then an energy
VAD (iter_002), with priming/confidence machinery layered on top (iter_003-005).
All of them threw away a capability the backend exposes but the workspace has
never touched: **the timestamp tokens themselves.**

When ``<|notimestamps|>`` is omitted from the prompt, Whisper's decoder emits
timestamp tokens inline in ``sequences_ids[0]`` — special ids ≥ the ``<|0.00|>``
token, where ``time_seconds = (id - ts_begin) * 0.02``. These are the model's
own estimate of where each segment starts and ends. No prior iteration removed
``<|notimestamps|>``, so this entire return-channel content is unmapped surface.

This rewrite replaces the external VAD segmentation with Whisper's *native*
sequential algorithm: decode a 30s window, read the last emitted timestamp, and
advance ``seek`` to exactly that point so the next window begins on a model-
chosen boundary (a true utterance edge the model committed to), then re-decode
the audio after it. The window cut is therefore never mid-utterance by
construction — the model tells us where it is safe to cut, rather than an energy
heuristic guessing. This attacks the dominant **substitution** axis at its
source: boundary spans, where a window edge splits a word and the half-word is
mis-recognized, are eliminated because cuts land on the model's own segment
boundaries and the unconsumed tail is always re-decoded with full left context.

Stateless per window (no priming carry, no confidence gating) — this slot is a
DIVERGE explore, so the mechanism is isolated to the timestamp channel alone.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's hard mel window. Each generate() call sees at most this much audio.
_WINDOW_SECONDS = 30.0
# Whisper timestamp-token granularity: token (id - ts_begin) encodes this many
# seconds. Fixed by the model, not a tunable.
_TIME_PRECISION = 0.02
# Anti-stall floor. If the model's last timestamp implies the window advanced
# less than this, fall back to a full-window stride so seek can never get stuck
# re-decoding the same span. This is the standard sequential-decode safeguard,
# not an error swallow — a stalled seek would silently truncate the recording.
_MIN_ADVANCE_S = 1.0


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    # Deliberately omit <|notimestamps|>: that single removal flips the decoder
    # into timestamp mode, emitting the segment-boundary tokens this pipeline
    # reads. Every prior iter kept notimestamps and never saw them.
    sot_prefix = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    # First timestamp token <|0.00|>; every id at or above it is a timestamp.
    ts_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    window_len = int(_WINDOW_SECONDS * sr)
    # Sub-100ms residue is silence, not signal worth a forward pass.
    min_samples = sr // 10
    total = len(audio)

    texts: list[str] = []
    seek = 0
    while seek < total:
        end = min(seek + window_len, total)
        chunk = audio[seek:end]
        if len(chunk) < min_samples:
            break
        is_final = end >= total

        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        res = generate(
            features,
            [list(sot_prefix)],
            beam_size=1,
            sampling_temperature=0.0,
        )
        ids = res[0].sequences_ids[0]

        # Positions of timestamp tokens in the emitted sequence. The decode
        # interleaves <|t0|> text <|t1|> <|t1|> text <|t2|> ... ; the last
        # timestamp marks how far into this window the model is confident.
        ts_positions = [k for k, t in enumerate(ids) if t >= ts_begin]

        # Degenerate cases — no usable closing boundary, or the final window:
        # commit all decoded text and advance a full window (or stop).
        if is_final or not ts_positions or ts_positions[-1] == 0:
            content = [t for t in ids if t < ts_begin]
            text = tokenizer.decode(content, skip_special_tokens=True).strip()
            if text:
                texts.append(text)
            if is_final:
                break
            seek += window_len
            continue

        last_pos = ts_positions[-1]
        consumed_s = (ids[last_pos] - ts_begin) * _TIME_PRECISION
        # Text up to the last committed boundary; the audio after it is left for
        # the next window so no span is decoded without full left context.
        content = [t for t in ids[:last_pos] if t < ts_begin]
        text = tokenizer.decode(content, skip_special_tokens=True).strip()
        if text:
            texts.append(text)

        advance = consumed_s if consumed_s >= _MIN_ADVANCE_S else _WINDOW_SECONDS
        seek += int(advance * sr)

    return " ".join(texts)
