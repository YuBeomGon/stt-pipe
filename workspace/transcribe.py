"""Workspace transcribe — autoresearch evolves this file.

Initial stub: one 30-second window through the frozen Whisper backend.
Long-form audio (most of 0715) will get its tail truncated; that is the
starting point autoresearch is supposed to improve.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"


_CHUNK_SECONDS = 30


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    chunk_len = _CHUNK_SECONDS * sr
    pieces = []
    for start in range(0, len(audio), chunk_len):
        chunk = audio[start : start + chunk_len]

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        pieces.append(
            processor.tokenizer.decode(token_ids, skip_special_tokens=True)
        )

    return " ".join(piece.strip() for piece in pieces if piece.strip())
