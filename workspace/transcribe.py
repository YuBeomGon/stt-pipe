"""Workspace transcribe — autoresearch evolves this file.

Chunked decode: split audio into Whisper's native 30-second windows and
decode each sequentially (batch=1), then join. Whisper's feature extractor
pads/truncates every call to a fixed 30s window, so a single call drops
everything past the first 30s — the dominant CER source on long-form 0715
audio. Sequential per-chunk decode recovers the tail while staying within
GPU memory (a batched all-chunks-at-once decode overflows VRAM).

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's fixed analysis window is 30s; chunk the waveform to match it.
_CHUNK_SECONDS = 30


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    chunk_len = _CHUNK_SECONDS * sr
    if chunk_len <= 0 or audio.shape[0] == 0:
        return ""

    texts: list[str] = []
    for start in range(0, audio.shape[0], chunk_len):
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
        texts.append(
            processor.tokenizer.decode(token_ids, skip_special_tokens=True)
        )

    return " ".join(t.strip() for t in texts if t.strip())
