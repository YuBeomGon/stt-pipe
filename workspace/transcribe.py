"""Workspace transcribe — timestamp-conditioned sliding window (long-form).

Diverges from fixed-stride 30 s windowing. The previous pipeline cut the
waveform into back-to-back 30 s windows and decoded each greedily with
``<|notimestamps|>``. That deletes audio whenever a window's content ends
before the 30 s mark: the model emits EOT early (or collapses into a repetition
loop) yet the read head still jumps a full 30 s, skipping the unread tail —
exactly the coverage/deletion failure (del 80 %, length_ratio 0.65) the
diagnosis reports.

This enables Whisper's native timestamp tokens (the prompt drops
``<|notimestamps|>``) and uses the model's *own* last predicted timestamp to
advance the read head: after decoding the 30 s window at ``seek``, the final
timestamp token says how far into the window the model actually transcribed, and
the next window starts exactly there instead of at ``seek + 30 s``. No audio
between a window's true end and the 30 s boundary is skipped, so under-attended
or early-terminated windows no longer delete their tails. The timestamp tokens
are the backend return channel the fixed-stride pipeline threw away.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
_TIME_PRECISION = 0.02  # seconds per timestamp-token step (Whisper convention)


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio).reshape(-1)
    if audio.shape[0] == 0:
        return ""

    # No <|notimestamps|>: the model now emits timestamp tokens we steer on.
    prompt_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    window = _WINDOW_SECONDS * sr
    n = audio.shape[0]
    seek = 0
    pieces = []

    while seek < n:
        chunk = audio[seek : seek + window]
        chunk_seconds = chunk.shape[0] / sr
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )
        token_ids = results[0].sequences_ids[0]

        # Text: linguistic tokens are all below the timestamp block, which sits
        # at the top of the vocab; drop timestamp + special tokens.
        text_ids = [t for t in token_ids if t < timestamp_begin]
        text = tokenizer.decode(text_ids, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

        # Advance by the model's own last predicted timestamp within this window.
        timestamps = [t for t in token_ids if t >= timestamp_begin]
        advance = window
        if timestamps:
            last_time = (timestamps[-1] - timestamp_begin) * _TIME_PRECISION
            if 0.0 < last_time <= chunk_seconds:
                advance = int(round(last_time * sr))

        # Guarantee forward progress so a degenerate (near-zero) timestamp
        # cannot stall the read head; fall back to a full stride.
        if advance < sr:
            advance = window
        seek += advance

    return " ".join(pieces)
