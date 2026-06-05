"""Workspace transcribe — autoresearch evolves this file.

Silence-aware long-form windowing. Whisper's feature extractor pads/truncates
to a single 30-second mel window, so a multi-minute call must be sliced. The
prior pipeline cut on a blind 30s grid, which slices straight through whatever
word/utterance happens to straddle each 30s mark — corrupting the tail of one
window and the head of the next and feeding the dominant deletion axis. The
ledger (iter_003) established that any window <=30s zero-pads to the full mel
for free, so the *cut placement* is an unconstrained knob. We use it: an RMS
envelope is computed from the waveform and each boundary is snapped to the
quietest frame in the last few seconds before the 30s ceiling, so cuts land in
pauses between utterances rather than inside speech. Variable-length windows
(<=30s) are still decoded in small fixed-size batches to bound GPU memory.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's mel front-end is fixed at 30 seconds: hard ceiling per window.
_WINDOW_SECONDS = 30
# How far back from the 30s ceiling to hunt for a silent cut point. A cut is
# therefore always placed in [ceiling-search, ceiling], i.e. a 25-30s window.
_CUT_SEARCH_SECONDS = 5
# RMS envelope hop (20ms frames) — fine enough to find inter-utterance pauses,
# coarse enough that the whole envelope is cheap on a 40-minute call.
_ENERGY_HOP_SECONDS = 0.02
# Cap concurrent windows per generate() call so GPU memory stays bounded.
_MAX_WINDOWS_PER_BATCH = 4


def _silence_aware_windows(audio: np.ndarray, sr: int) -> list[np.ndarray]:
    """Slice audio into <=30s windows, snapping each cut to a local energy min."""
    max_win = _WINDOW_SECONDS * sr
    n = len(audio)
    if n <= max_win:
        return [audio]

    hop = max(1, int(_ENERGY_HOP_SECONDS * sr))
    n_frames = n // hop
    frames = audio[: n_frames * hop].reshape(n_frames, hop).astype(np.float64)
    energy = np.sqrt(np.mean(frames * frames, axis=1))

    search = _CUT_SEARCH_SECONDS * sr
    windows: list[np.ndarray] = []
    start = 0
    while start < n:
        if start + max_win >= n:
            windows.append(audio[start:n])
            break
        ceiling = start + max_win
        lo = ceiling - search
        f_lo, f_hi = lo // hop, ceiling // hop
        local = energy[f_lo:f_hi]
        # Cut at the quietest frame in the search band (a pause); fall back to
        # the hard ceiling if that band is empty.
        cut = (f_lo + int(np.argmin(local))) * hop if local.size else ceiling
        if cut <= start:
            cut = ceiling
        windows.append(audio[start:cut])
        start = cut
    return windows


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    audio = np.asarray(audio).reshape(-1)
    chunks = _silence_aware_windows(audio, sr)
    if not chunks:
        chunks = [audio]

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    pieces: list[str] = []
    for batch_start in range(0, len(chunks), _MAX_WINDOWS_PER_BATCH):
        batch_chunks = chunks[batch_start : batch_start + _MAX_WINDOWS_PER_BATCH]
        feats = [
            processor(chunk, sampling_rate=sr, return_tensors="np").input_features[0]
            for chunk in batch_chunks
        ]
        batch = to_storage_view(np.stack(feats, axis=0))

        # REFINE on the greedy+segmentation best (iter_011): keep greedy decode
        # (beam ablated, coverage owned by silence-aware cuts) and add a WIDE
        # exact-cycle block. The parent's no_repeat_ngram_size=5 was scored in
        # the coverage-starved blind-grid regime (length_ratio 0.65) where the
        # ledger (iter_006/007) found ANY n-gram block net-negative — it traded
        # away scarce coverage. That objection is regime-dependent: post-segmentation
        # coverage is 0.94 and the dominant axis flipped to substitution, so the
        # collateral block of natural recurring Korean grams no longer starves
        # the window tail. Both focus files are repeated_text guard violations
        # (clause-level loops, >=6 subword tokens), so n=6 still catches the real
        # repetition-collapse loop while sparing the natural <=5-grams whose
        # blocking would otherwise ADD substitutions on the clean files. Greedy
        # is kept, so runtime is unchanged (ngram filtering is decode-cheap).
        results = generate(
            batch,
            [prompt_tokens] * len(batch_chunks),
            sampling_temperature=0.0,
            no_repeat_ngram_size=6,
        )

        for r in results:
            piece = processor.tokenizer.decode(
                r.sequences_ids[0], skip_special_tokens=True
            ).strip()
            if piece:
                pieces.append(piece)

    return " ".join(pieces)
