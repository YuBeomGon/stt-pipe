"""Workspace transcribe — autoresearch evolves this file.

Discovery (iter 1): the feature extractor truncates audio to a single
30-second window (3000 mel frames), so every multi-minute 0715 call loses
its entire tail — a large, structural deletion source. We window the full
audio ourselves and decode each chunk. Decoding ALL windows in one batched
generate call (iter 1) exhausted CUDA memory on long calls; here each window
is decoded in its own batch-of-1 call so peak GPU memory stays bounded.

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
    n_samples = audio.shape[0]
    starts = list(range(0, max(n_samples, 1), window))

    texts = []
    for start in starts:
        chunk = audio[start : start + window]
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
        text = processor.tokenizer.decode(token_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text.strip())

    return " ".join(texts)
