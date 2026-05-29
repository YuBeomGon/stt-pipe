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


_CHUNK_SECONDS = 30
_SNAP_SEARCH_SECONDS = 3.0
_SNAP_WINDOW_SECONDS = 0.05


def _silence_snapped_boundaries(audio: np.ndarray, sr: int) -> list[int]:
    """Chunk boundaries near each 30s mark, snapped to the lowest-energy
    (quietest) sample within a backward-only search window (target-3s .. target)
    so cuts land in silence instead of mid-word *and* never exceed the 30s
    Whisper window. Searching forward could place a cut up to 3s past the 30s
    mark, but the feature extractor truncates anything beyond 30s, silently
    dropping the chunk tail (the systematic deletion seen as length_ratio<1).
    Audio remains fully covered."""
    n = len(audio)
    chunk_len = _CHUNK_SECONDS * sr
    if n <= chunk_len:
        return [0, n]

    csum = np.concatenate(([0.0], np.cumsum(audio.astype(np.float64) ** 2)))
    half = max(1, int(_SNAP_WINDOW_SECONDS * sr / 2))
    search = int(_SNAP_SEARCH_SECONDS * sr)
    step = max(1, int(0.01 * sr))

    boundaries = [0]
    pos = 0
    while pos + chunk_len < n:
        target = pos + chunk_len
        lo = max(pos + half + 1, target - search)
        hi = min(n - half - 1, target)
        if hi <= lo:
            cut = target
        else:
            cands = np.arange(lo, hi, step)
            energy = csum[cands + half] - csum[cands - half]
            cut = int(cands[int(np.argmin(energy))])
        boundaries.append(cut)
        pos = cut
    boundaries.append(n)
    return boundaries


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    pieces = []
    boundaries = _silence_snapped_boundaries(audio, sr)
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        chunk = audio[start:end]

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=4,
            patience=1.5,
            length_penalty=1.6,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        pieces.append(
            processor.tokenizer.decode(token_ids, skip_special_tokens=True)
        )

    return " ".join(piece.strip() for piece in pieces if piece.strip())
