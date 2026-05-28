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

    chunk_samples = 30 * sr
    n_chunks = max(1, (len(audio) + chunk_samples - 1) // chunk_samples)
    chunks = [audio[i * chunk_samples : (i + 1) * chunk_samples] for i in range(n_chunks)]

    inputs = processor(
        chunks,
        sampling_rate=sr,
        return_tensors="np",
    )
    features = to_storage_view(inputs.input_features)

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )
    prompts = [prompt_tokens] * n_chunks

    results = generate(
        features,
        prompts,
        beam_size=1,
        sampling_temperature=0.0,
    )

    texts = [
        processor.tokenizer.decode(r.sequences_ids[0], skip_special_tokens=True)
        for r in results
    ]
    return " ".join(texts)
