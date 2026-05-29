"""Workspace transcribe — autoresearch evolves this file.

Sequential 30s-window chunking: the frozen Whisper feature extractor pads or
truncates any input to a single 30s window, so the full-audio stub silently
deletes everything past the first 30 seconds on multi-minute 0715 calls. We
pre-slice the waveform into consecutive windows, decode each on its own, and
join the transcripts so the long-form tail is recovered.

Timestamp-anchored window advancement: iter_006 dropped ``<|notimestamps|>`` so
the decoder emits ``<|t|>`` segment markers, but it still threw those markers
away (``skip_special_tokens=True``) and advanced the window by a blind +30s.
That fixed-stride slicing cuts a segment mid-sentence at every window boundary,
and Whisper systematically declines to emit the truncated trailing segment —
the mechanism behind the residual universal ``length_ratio < 1`` (0.65–0.83 on
every file) that survived the timestamp switch.

This iter reads the timestamp token *values* (the surface iter_006 enabled but
never consumed). The decoded sequence ends each complete segment with a
timestamp token; we keep only the text up to the *last* such marker, drop the
truncated trailing segment, and advance ``seek`` to that marker's audio time so
the next window re-transcribes the cut-off tail from a clean segment start.
This is Whisper's native long-form loop and removes boundary deletion without
duplicating audio. The final window keeps its full text so the call's tail is
not dropped.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30
_TS_RESOLUTION = 0.02  # seconds per Whisper timestamp token
_MIN_ADVANCE_SECONDS = 2.0  # guard against degenerate tiny advances (progress + runtime)


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = processor.tokenizer.convert_tokens_to_ids("<|0.00|>")

    chunk_len = _CHUNK_SECONDS * sr
    min_advance = int(_MIN_ADVANCE_SECONDS * sr)
    n = len(audio)

    texts: list[str] = []
    seek = 0
    while seek < n:
        chunk = audio[seek : seek + chunk_len]
        is_last = seek + chunk_len >= n

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=5,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]

        # Index of the last segment-closing timestamp token in this window.
        last_ts_idx = None
        for i in range(len(token_ids) - 1, -1, -1):
            if token_ids[i] >= timestamp_begin:
                last_ts_idx = i
                break

        # Final window (or no usable timestamp): keep everything and finish/step
        # the full window so no audio is left unprocessed.
        if is_last or last_ts_idx is None:
            texts.append(
                processor.tokenizer.decode(token_ids, skip_special_tokens=True)
            )
            seek += chunk_len
            continue

        advance = int((token_ids[last_ts_idx] - timestamp_begin) * _TS_RESOLUTION * sr)
        if advance < min_advance:
            # Degenerate: model anchored its last complete segment too early to
            # make progress — fall back to the full window rather than crawl.
            texts.append(
                processor.tokenizer.decode(token_ids, skip_special_tokens=True)
            )
            seek += chunk_len
            continue

        # Keep only the complete segments (text before the final marker); the
        # truncated trailing segment is re-decoded by the next, re-anchored window.
        kept = token_ids[:last_ts_idx]
        texts.append(processor.tokenizer.decode(kept, skip_special_tokens=True))
        seek += advance

    return " ".join(t.strip() for t in texts if t.strip())
