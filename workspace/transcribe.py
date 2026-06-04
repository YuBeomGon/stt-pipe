"""Workspace transcribe — autoresearch evolves this file.

Discovery (iter 1, explore): the stub decoded a single 30 s window, so every
long-form 0715 call lost everything past 0:30 — a pure deletion sink. The
batched fix (iter 1) decoded all N windows in one ``generate`` call and OOM'd:
CT2 Whisper allocates encoder+decoder state for the whole batch at once, so a
long call (tens of 30 s rows) blows past VRAM.

Repair: keep the full-audio windowing, but decode the windows **sequentially**
— one feature row per ``generate`` call — so peak VRAM stays at single-window
cost. Slower wall-clock than the batch, but it actually runs and still recovers
all the audio past 0:30.

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

    audio = np.asarray(audio).reshape(-1)
    window = _WINDOW_SECONDS * sr
    chunks = [audio[start : start + window] for start in range(0, len(audio), window)]
    if not chunks:
        chunks = [audio]

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    texts = []
    for chunk in chunks:
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
