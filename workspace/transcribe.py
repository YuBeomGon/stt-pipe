"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prior iteration — beam/MBR/lexicon rerank, temperature
fallback, cross-window context priming, dual front-end, per-token align
excision, ROVER sampling vote — operates *downstream of a fixed segmentation*:
the audio is cut on a flat 30 s timeline and the cursor advances by either the
window length or the last emitted timestamp token. The segmentation itself has
never been conditioned on the acoustic signal. That is the structurally unused
input here: ``audio`` is a backend input I fully control before it ever reaches
``processor()``.

The diagnosis shows the eval calls have wildly non-uniform speech structure
(speech segments 2 s … 233 s, silence gaps p95 ~2–5 s, silence ratios 6–25 %).
A fixed 30 s cut therefore lands *inside utterances* — and a word bisected by a
window boundary is decoded from only half its acoustic evidence on each side,
with the other half replaced by the adjacent (often different) speaker turn.
That is a direct, never-probed source of the dominant 57 % substitution axis:
the boundary tokens are mis-recognised not because the audio is band-degraded
but because the *window cut destroyed the token's context*.

This slot replaces the timeline-advance windowing with **energy-VAD
segmentation**: compute a short-time RMS envelope over the raw waveform, mark
low-energy frames as silence, and cut the audio into decode segments *only
inside silence runs* — never through speech. Each segment is then a complete
acoustic unit (capped at the model's 30 s context, with the cut placed at the
quietest gap inside the admissible range). No word is bisected, so every token
is decoded with its full surrounding context. This is a fundamentally different
pipeline than the fixed-window incumbent — the backend's return channels are
read minimally (single beam decode, text tokens only); the lever is *where the
audio is cut*, not how the resulting decode is scored or fused.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

_BEAM_SIZE = 5

# Hard ceiling on a decode segment: the Whisper encoder context is 30 s, so a
# segment may never exceed it. Keep a small margin below 30 s.
_MAX_SEGMENT_S = 28.0
# Below this a silence gap is too short to be a safe utterance boundary, and a
# segment shorter than this is not worth its own 30 s-padded encode.
_MIN_SEGMENT_S = 4.0

# Short-time RMS envelope geometry.
_FRAME_S = 0.025
_HOP_S = 0.010
# A silence run must span at least this long to count as a cut-eligible gap —
# bridges over the brief intra-word stops that energy alone would flag.
_MIN_SILENCE_S = 0.30
# Silence threshold anchored to the call's SPEECH level, not a quantile of the
# whole envelope. A fixed percentile (iter_093) is silence-ratio-dependent: the
# eval calls span silence_ratio 6%..25%, so the 25th-percentile RMS migrates —
# on low-silence calls it lands inside speech energy and marks quiet-but-voiced
# frames (low-energy Korean particles / sentence-final endings) as silence,
# admitting mid-utterance cuts. Instead take a robust loud reference (the
# 95th-percentile RMS ≈ the speech mode) and place the floor a fixed fraction
# below it: this still adapts to each call's gain but is pinned RELATIVE TO
# SPEECH, so it does not drift into voiced frames as the silence ratio varies.
# 0.12 ≈ -18 dB below the speech peak — only genuinely quiet frames qualify.
_SILENCE_SPEECH_FRACTION = 0.12


def _frame_rms(audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Vectorised short-time RMS via a cumulative sum of squares."""
    hop = max(1, int(_HOP_S * sr))
    frame = max(hop, int(_FRAME_S * sr))
    n = audio.shape[0]
    if n < frame:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.int64), hop

    sq = np.empty(n + 1, dtype=np.float64)
    sq[0] = 0.0
    np.cumsum(audio.astype(np.float64) ** 2, out=sq[1:])

    starts = np.arange(0, n - frame + 1, hop, dtype=np.int64)
    energy = sq[starts + frame] - sq[starts]
    rms = np.sqrt(energy / frame)
    return rms, starts, hop


def _silence_gaps(rms: np.ndarray, starts: np.ndarray, hop: int) -> list[int]:
    """Sample indices at the centre of each cut-eligible silence run."""
    if rms.shape[0] == 0:
        return []

    speech_ref = np.percentile(rms, 95.0)
    thr = speech_ref * _SILENCE_SPEECH_FRACTION
    silent = rms <= thr
    min_silent_frames = max(1, int(_MIN_SILENCE_S / _HOP_S))

    gaps: list[int] = []
    i = 0
    nframes = silent.shape[0]
    while i < nframes:
        if silent[i]:
            j = i
            while j < nframes and silent[j]:
                j += 1
            if (j - i) >= min_silent_frames:
                mid_frame = (i + j) // 2
                gaps.append(int(starts[mid_frame] + hop // 2))
            i = j
        else:
            i += 1
    return gaps


def _segment_bounds(audio: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Greedy cut points: extend each segment to the farthest silence gap that
    falls within [start+min, start+max]; if none, hard-cut at start+max."""
    n = audio.shape[0]
    max_samp = int(_MAX_SEGMENT_S * sr)
    min_samp = int(_MIN_SEGMENT_S * sr)
    rms, starts, hop = _frame_rms(audio, sr)
    gaps = _silence_gaps(rms, starts, hop)

    bounds: list[tuple[int, int]] = []
    start = 0
    gi = 0
    while start < n:
        if n - start <= max_samp:
            bounds.append((start, n))
            break

        lo = start + min_samp
        hi = start + max_samp
        # advance gap pointer past gaps before the admissible window
        while gi < len(gaps) and gaps[gi] < lo:
            gi += 1
        # farthest gap still within [lo, hi]
        cut = -1
        k = gi
        while k < len(gaps) and gaps[k] <= hi:
            cut = gaps[k]
            k += 1
        if cut < 0:
            # No silence RUN clears the contiguity gate in [lo, hi] — common on
            # the eval's long monologue calls (longest_speech_s 149-233 s),
            # where every admissible window is wall-to-wall voiced. The parent
            # then hard-cuts at exactly `hi`, an acoustically-blind boundary
            # that bisects whatever word straddles start+max and decodes its
            # two halves from adjacent context — re-injecting the dominant 57 %
            # substitution. The RMS envelope is already computed; aim the
            # forced cut at the QUIETEST frame in [lo, hi] (a local energy
            # trough, even if too short to be a true gap) so the inevitable cut
            # lands at the lowest-energy point available rather than at an
            # arbitrary index.
            if starts.shape[0]:
                in_range = (starts >= lo) & (starts <= hi)
                if in_range.any():
                    masked = np.where(in_range, rms, np.inf)
                    cut = int(starts[int(np.argmin(masked))])
            if cut < 0:
                cut = hi
        bounds.append((start, cut))
        start = cut
    return bounds


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    n_samples = audio.shape[0]
    if n_samples == 0:
        return ""

    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")
    sot_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )

    pieces: list[str] = []
    for start, end in _segment_bounds(audio, sr):
        chunk = audio[start:end]
        if chunk.shape[0] == 0:
            continue

        inputs = processor(
            [chunk],
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        res = generate(
            features,
            [sot_tokens],
            beam_size=_BEAM_SIZE,
            sampling_temperature=0.0,
        )[0]

        token_ids = res.sequences_ids[0]
        text_tokens = [t for t in token_ids if t < timestamp_begin]
        text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

    return " ".join(pieces)
