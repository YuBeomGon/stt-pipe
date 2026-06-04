"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing: Whisper's feature extractor pads/truncates every input
to a fixed 30-second mel window, so the stub transcribed only the first 30s of
each call and deleted the entire tail. We split the call into 30s windows and
decode them, then concatenate the segment transcripts.

The first windowing attempt fed *every* window of a call into one batched
``generate`` call; long calls have tens of windows, so the parallel decode
exhausted GPU memory (CUDA out of memory). We instead decode one window at a
time — the tail is still recovered, but peak memory is bounded to a single
30s window regardless of call length.

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

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    window = _WINDOW_SECONDS * sr
    n_windows = max(1, int(np.ceil(len(audio) / window)))
    chunks = [audio[i * window : (i + 1) * window] for i in range(n_windows)]

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    segments = []
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
        segments.append(
            processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        )

    return " ".join(s for s in segments if s)
