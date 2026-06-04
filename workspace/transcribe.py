"""Workspace transcribe — autoresearch evolves this file.

Overlapping timestamp-guided long-form decoding. The incumbent advanced ``seek``
to the *last* timestamp a window emitted and emitted text up to it — so the
final segment of every window was the one Whisper decoded with the least
right-context (the model was still "mid-thought" at the window's trailing edge).
With coverage now healthy (length_ratio 0.94) the dominant error has shifted to
*substitution* (56%): the model hears speech and emits roughly the right length
but the wrong characters. A natural substitution source on this axis is exactly
that right-edge, context-starved segment.

This pipeline ends each window one segment EARLY: it emits up to the
*penultimate* timestamp and advances ``seek`` there, so the trailing
(context-starved) segment is re-decoded as an *interior* segment of the next
window — this time with full right-context. Every segment thus gets at least one
decode where it is not the window's trailing edge. The re-read is bounded
(``_MIN_OVERLAP_ADVANCE``): we only drop-and-re-read when the penultimate
boundary is late enough that the dropped tail is short, so window count grows by
at most ~1.5x and peak memory is still a single 30s window.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30.0
_TIME_PRECISION = 0.02  # Whisper timestamp token resolution, seconds per step.
_MIN_ADVANCE_SECONDS = 1.0  # below this the timestamp is untrustworthy.
# Only drop+re-read the trailing segment when the penultimate boundary is at
# least this far into the window. This keeps the re-read tail short, bounds the
# extra window count (advance >= 20s => <=1.5x windows), and avoids re-decoding
# a genuinely long final segment (which is not an edge artifact).
_MIN_OVERLAP_ADVANCE_SECONDS = 20.0


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    # Timestamps ON: omit <|notimestamps|>. The decoder interleaves <|t|>
    # tokens between segments — the channel we both seek and trim with.
    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = processor.tokenizer.convert_tokens_to_ids("<|0.00|>")

    audio = np.asarray(audio).reshape(-1)
    n = audio.shape[0]
    chunk_len = int(_CHUNK_SECONDS * sr)
    min_advance = int(_MIN_ADVANCE_SECONDS * sr)
    min_overlap_advance = int(_MIN_OVERLAP_ADVANCE_SECONDS * sr)

    texts: list[str] = []
    seek = 0
    while seek < n:
        chunk = audio[seek : seek + chunk_len]
        if chunk.shape[0] == 0:
            break
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )
        token_ids = results[0].sequences_ids[0]

        ts_positions = [i for i, t in enumerate(token_ids) if t >= timestamp_begin]

        def _advance_at(pos: int) -> int:
            return int((token_ids[pos] - timestamp_begin) * _TIME_PRECISION * sr)

        advance = chunk_len
        emit_ids = token_ids

        # Preferred boundary: the PENULTIMATE timestamp. Ending one segment
        # early hands the right-edge (context-starved) segment to the next
        # window, where it is re-decoded with right-context. Only do this when
        # the penultimate boundary is late enough (short dropped tail) so the
        # re-read stays cheap.
        if len(ts_positions) >= 3:
            pen_i = ts_positions[-2]
            pen_advance = _advance_at(pen_i)
            if pen_advance >= min_overlap_advance:
                advance = pen_advance
                emit_ids = token_ids[:pen_i]

        # Fallback: last-timestamp boundary (no overlap) when the penultimate
        # boundary was too early to re-read cheaply, or too few timestamps to
        # define a droppable tail. Guards the loop against degenerate windows.
        if advance == chunk_len and len(ts_positions) >= 2:
            last_i = ts_positions[-1]
            last_advance = _advance_at(last_i)
            if last_advance >= min_advance:
                advance = last_advance
                emit_ids = token_ids[:last_i]

        text = processor.tokenizer.decode(emit_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text.strip())

        seek += advance

    return " ".join(texts)
