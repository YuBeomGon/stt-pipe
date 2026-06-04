"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing pipeline: the Whisper feature extractor emits a single
fixed 30-second log-mel window, so the stub discarded everything past the
first 30s of each recording. Here the raw waveform is sliced into consecutive
30s windows and each window is decoded with its OWN ``generate`` call, so peak
GPU memory stays at one window regardless of recording length — the batched
form OOM'd on the long 0715 call-center recordings. The per-window decodes are
concatenated, recovering the truncated tail.

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

    audio = np.asarray(audio).reshape(-1)
    chunk_len = int(_CHUNK_SECONDS * sr)
    n_samples = audio.shape[0]

    texts = []
    for start in range(0, max(n_samples, 1), chunk_len):
        chunk = audio[start : start + chunk_len]
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
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
