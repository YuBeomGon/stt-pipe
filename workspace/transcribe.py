"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing (sequential): the single-window stub fed only the first
30 s of each call to the processor (Whisper truncates/pads features to a fixed
3000-frame / 30 s window), so the entire tail of every >30 s recording was
deleted. This splits the raw waveform into consecutive <=30 s windows and
decodes them ONE AT A TIME, concatenating the transcripts. The prior batched
form stacked every window of a clip into a single ``generate`` call, which ran
the GPU out of memory on long calls — sequential decoding caps peak memory at
one 30 s window regardless of clip length.

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
    if audio.shape[0] == 0:
        return ""

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    window = _WINDOW_SECONDS * sr
    pieces = []
    for start in range(0, audio.shape[0], window):
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
        text = processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

    return " ".join(pieces)
