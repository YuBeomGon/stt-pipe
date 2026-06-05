"""Workspace transcribe — autoresearch evolves this file.

Silence-aware (energy-VAD) segmentation. The previous pipeline blind-strided
the signal in fixed 30s windows: it *did* tile the whole recording (no raw
samples skipped), yet still scored deletion-heavy (del 80%, length_ratio 0.65)
with a high repeated-text rate (0.36). Re-diagnosis: those deletions are not
dropped audio — they are *within-window under-generation*. A 30s window cut at
an arbitrary sample lands mid-utterance and hands Whisper a dense, boundary-
split span, which on this Korean call-center audio collapses into repetition
and early EOS, emitting far less text than the span contains.

So the binding mechanism is *where* we cut, not whether we cover. This rewrites
segmentation: a cheap numpy energy VAD finds silence runs and we tile the whole
signal with speech-aligned chunks (≤28s) whose boundaries fall at the centre of
a silence gap whenever one exists within reach. Each decode call then sees a
self-contained, silence-bounded span instead of a mid-word fragment, which is
the structural fix for collapse-driven deletion. Coverage is still total (the
chunks partition [0, len) exactly); only the boundary placement changes.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Stay under Whisper's hard 30s mel window, with headroom so a silence-aligned
# chunk never overflows the forward pass.
_MAX_CHUNK_SECONDS = 28.0
# Energy VAD frame size.
_FRAME_MS = 30.0
# Minimum silence-run length that qualifies as a safe cut point.
_MIN_SILENCE_S = 0.35


def _silence_aligned_bounds(audio: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Partition [0, len(audio)) into chunks <= max, cutting at silence centres.

    Energy per ``_FRAME_MS`` frame is compared to a low percentile of the
    file's own energy distribution to mark silent frames; silence runs longer
    than ``_MIN_SILENCE_S`` become candidate cut points. We then greedily take
    the farthest candidate within one max-chunk of the current start, falling
    back to a hard cut at the max length only when no silence is in reach.
    """
    max_len = int(_MAX_CHUNK_SECONDS * sr)
    frame = int(sr * _FRAME_MS / 1000.0)
    if frame <= 0 or len(audio) <= max_len:
        return [(0, len(audio))]

    n = len(audio) // frame
    frames = audio[: n * frame].reshape(n, frame).astype(np.float64)
    energy = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    # Threshold between the file's noise floor and its speech energy. The 30th
    # percentile sits inside silence for typical call-center duty cycles.
    thr = np.percentile(energy, 30)
    silent = energy <= thr

    min_sil_frames = max(1, int(_MIN_SILENCE_S / (_FRAME_MS / 1000.0)))
    cuts: list[int] = []
    i = 0
    while i < n:
        if silent[i]:
            j = i
            while j < n and silent[j]:
                j += 1
            if j - i >= min_sil_frames:
                cuts.append(((i + j) // 2) * frame)
            i = j
        else:
            i += 1

    bounds: list[tuple[int, int]] = []
    start = 0
    total = len(audio)
    while start < total:
        limit = start + max_len
        if limit >= total:
            bounds.append((start, total))
            break
        reachable = [c for c in cuts if start < c <= limit]
        end = max(reachable) if reachable else limit
        bounds.append((start, end))
        start = end
    return bounds


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    # A chunk shorter than this is sub-100ms of real signal — only silence
    # hallucination lives there.
    min_samples = sr // 10

    texts: list[str] = []
    for start, end in _silence_aligned_bounds(audio, sr):
        chunk = audio[start:end]
        if len(chunk) < min_samples:
            continue

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
