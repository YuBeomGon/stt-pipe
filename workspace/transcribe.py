"""Workspace transcribe — autoresearch evolves this file.

Explore rewrite: full-coverage sliding-window long-form transcription.
The previous stub fed a single 30s window to the backend, so every 0715
call longer than 30 seconds had its tail silently truncated (pure deletion
error). Here we tile the entire audio into consecutive 30-second frames,
decode each through the frozen backend, and join the pieces — so the
pipeline actually transcribes the whole recording.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    window = _WINDOW_SECONDS * sr
    min_tail = int(0.1 * sr)  # ignore sub-100ms trailing fragment

    pieces: list[str] = []
    for start in range(0, len(audio), window):
        chunk = audio[start : start + window]
        if len(chunk) < min_tail:
            continue

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=5,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        piece = processor.tokenizer.decode(token_ids, skip_special_tokens=True)
        if piece.strip():
            pieces.append(piece.strip())

    return " ".join(pieces)
