"""Workspace transcribe — autoresearch evolves this file.

Long-form chunked decoding: the audio is split into consecutive 30-second
windows and each is decoded through the frozen Whisper backend, then the
window transcripts are concatenated. This replaces the single-window stub,
whose only call truncated everything past the first 30s (a pure deletion
failure on the long 0715 call-center recordings).

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's feature extractor pads/truncates each forward pass to a fixed
# 30-second mel window, so a single generate() call can never see beyond 30s.
# To cover the whole recording we slice the raw signal into 30s windows and
# decode each independently.
_WINDOW_SECONDS = 30


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    window_len = _WINDOW_SECONDS * sr
    # A trailing slice shorter than this is feature-extractor padding territory
    # (sub-100ms of real signal); decoding it only invites silence hallucination.
    min_samples = sr // 10

    window_texts: list[str] = []
    for start in range(0, len(audio), window_len):
        window = audio[start : start + window_len]
        if len(window) < min_samples:
            continue

        inputs = processor(
            window,
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
            window_texts.append(text.strip())

    return " ".join(window_texts)
