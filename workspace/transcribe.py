"""Workspace transcribe — autoresearch evolves this file.

Windowed segmentation: the audio is split into fixed 30-second frames, each
frame is decoded through the frozen Whisper backend, and the partial texts are
concatenated. This recovers the tail of long-form 0715 calls that the original
single-window stub silently truncated (a pure deletion failure).

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's receptive field is a fixed 30 s mel window; longer audio must be
# fed as multiple windows.
_WINDOW_SECONDS = 30
# Decode windows in batches so the GPU stays busy and we keep within budget
# instead of paying per-window launch latency on dozens of frames.
_BATCH = 8


def _window_bounds(n_samples: int, win: int) -> list[tuple[int, int]]:
    if n_samples <= 0:
        return [(0, 0)]
    return [(s, min(s + win, n_samples)) for s in range(0, n_samples, win)]


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    audio = np.asarray(audio, dtype=np.float32)
    win_samples = int(_WINDOW_SECONDS * sr)
    bounds = _window_bounds(audio.shape[0], win_samples)

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    pieces: list[str] = []
    for batch_start in range(0, len(bounds), _BATCH):
        batch_bounds = bounds[batch_start : batch_start + _BATCH]
        chunks = [audio[s:e] for s, e in batch_bounds]

        # The processor pads/truncates each chunk to the fixed 30 s mel window,
        # so stacking gives a clean (N, n_mels, n_frames) feature batch.
        inputs = processor(
            chunks,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens] * len(chunks),
            beam_size=1,
            sampling_temperature=0.0,
        )

        for res in results:
            token_ids = res.sequences_ids[0]
            pieces.append(
                processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
            )

    return " ".join(p for p in pieces if p)
