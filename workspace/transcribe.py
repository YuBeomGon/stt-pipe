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


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    # Whisper's feature extractor pads/truncates to a single 30s window, so the
    # stub silently drops everything past the first 30s. Window the full signal
    # into consecutive 30s chunks so multi-minute calls are covered end to end.
    chunk_samples = 30 * sr
    chunks = [
        audio[start : start + chunk_samples]
        for start in range(0, len(audio), chunk_samples)
    ]
    chunks = [c for c in chunks if len(c) > 0]
    if not chunks:
        return ""

    # The previous attempt stacked ALL windows into one batched decode, which
    # OOMs on multi-minute calls (an (N,80,3000) view all resident at once).
    # Decode each 30s window in its own generate() call so peak GPU memory
    # stays at single-window size regardless of file length.
    texts = []
    for chunk in chunks:
        features = to_storage_view(
            processor(chunk, sampling_rate=sr, return_tensors="np").input_features
        )
        results = generate(
            features,
            [prompt_tokens],
            beam_size=5,
            sampling_temperature=0.0,
        )
        text = processor.tokenizer.decode(
            results[0].sequences_ids[0], skip_special_tokens=True
        )
        if text.strip():
            texts.append(text.strip())

    return " ".join(texts)
