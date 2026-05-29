"""Workspace transcribe — autoresearch evolves this file.

Sequential 30s-window chunking: the frozen Whisper feature extractor pads or
truncates any input to a single 30s window, so the full-audio stub silently
deletes everything past the first 30 seconds on multi-minute 0715 calls. We
pre-slice the waveform into consecutive 30s windows, decode each on its own,
and join the transcripts so the long-form tail is recovered.

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
    texts: list[str] = []
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
            beam_size=5,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        texts.append(processor.tokenizer.decode(token_ids, skip_special_tokens=True))

    return " ".join(t.strip() for t in texts if t.strip())
