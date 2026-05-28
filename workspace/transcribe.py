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

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    texts = []
    for i in range(n_chunks):
        chunk = audio[i * chunk_samples : (i + 1) * chunk_samples]
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)
        results = generate(
            features,
            [prompt_tokens],
            beam_size=5,
            sampling_temperature=0.0,
        )
        text = processor.tokenizer.decode(results[0].sequences_ids[0], skip_special_tokens=True)
        texts.append(text)

    return " ".join(texts)
