"""Workspace transcribe — autoresearch evolves this file.

Timestamp-conditioned sequential long-form decoding.

The stub decoded a single fixed 30s mel window with ``<|notimestamps|>`` and
dropped everything past 30s (deletion-dominated CER). Prior long-form attempts
sliced audio on a fixed grid and fought the seams with overlap + string
stitching. This instead removes ``<|notimestamps|>`` so the decode emits
timestamp tokens, and uses the *last* emitted timestamp to advance the window
to a model-chosen segment boundary — the canonical Whisper sequential loop.
Each seam falls on a boundary the model itself picked, so there is nothing to
stitch and nothing duplicated.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SEC = 30.0
_TS_RESOLUTION = 0.02  # Whisper timestamp token granularity (seconds)


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    prompt_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    window = int(_WINDOW_SEC * sr)
    n = len(audio)
    seek = 0
    pieces: list[str] = []

    while seek < n:
        chunk = audio[seek : seek + window]
        is_last = (seek + window) >= n

        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=3,
            patience=1.0,
            length_penalty=1.0,
            sampling_temperature=0.0,
        )
        tokens = results[0].sequences_ids[0]

        # Locate the last timestamp token: it marks the model's chosen
        # boundary for where this window's reliable transcription ends.
        last_ts_pos = -1
        for i in range(len(tokens) - 1, -1, -1):
            if tokens[i] >= timestamp_begin:
                last_ts_pos = i
                break

        if last_ts_pos == -1 or is_last:
            # No boundary (or final chunk): commit all text, advance fully.
            text_ids = [t for t in tokens if t < timestamp_begin]
            advance = window
        else:
            # Commit only text up to the boundary; the tail after it belongs
            # to the next window and will be re-decoded there.
            text_ids = [t for t in tokens[:last_ts_pos] if t < timestamp_begin]
            boundary_sec = (tokens[last_ts_pos] - timestamp_begin) * _TS_RESOLUTION
            advance = int(boundary_sec * sr)

        piece = tokenizer.decode(text_ids, skip_special_tokens=True)
        if piece:
            pieces.append(piece)

        if is_last:
            break
        # Guard against a degenerate boundary (0s) stalling the loop.
        if advance <= 0:
            advance = window
        seek += advance

    return " ".join(p.strip() for p in pieces if p.strip())
