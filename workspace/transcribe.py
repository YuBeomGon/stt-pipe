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
_PREV_MAX_TOKENS = 200


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    chunk_samples = 30 * sr
    overlap_samples = 5 * sr
    step_samples = chunk_samples - overlap_samples
    n_chunks = max(
        1, (max(0, len(audio) - overlap_samples) + step_samples - 1) // step_samples
    )

    sot_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )
    startofprev_id = processor.tokenizer.convert_tokens_to_ids("<|startofprev|>")

    prev_tokens: list[int] = []
    texts = []
    for i in range(n_chunks):
        chunk = audio[i * step_samples : i * step_samples + chunk_samples]
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        if prev_tokens:
            prompt = [startofprev_id] + prev_tokens[-_PREV_MAX_TOKENS:] + sot_tokens
        else:
            prompt = sot_tokens

        results = generate(
            features,
            [prompt],
            beam_size=5,
            length_penalty=2.0,
            patience=2.0,
            no_repeat_ngram_size=6,
            sampling_temperature=0.0,
        )
        out_tokens = list(results[0].sequences_ids[0])
        texts.append(processor.tokenizer.decode(out_tokens, skip_special_tokens=True))
        prev_tokens = out_tokens

    return " ".join(texts)
