"""Workspace transcribe — autoresearch evolves this file.

Coverage stays floored (~0.65 length_ratio) on *every* 0715 file, flagged or
not — a uniform boundary loss, not the per-window degeneracy iter_003 chased.
The cause is the fixed non-overlapping 30s stride: whatever utterance straddles
a window boundary is decoded across a hard cut, so its tail is dropped and its
head (in the next window) starts mid-word. Accumulated over ~80 windows/file
that is the systematic deletion.

Whisper's native long-form cure is *timestamp tokens*. Decoding without
``<|notimestamps|>`` makes the model emit ``<|t|>`` segment boundaries; the last
complete segment-end timestamp says how far the window was actually consumed.
Seeking the next window to that point (instead of a blind +30s) re-decodes the
trailing partial utterance from the start of the next window rather than cutting
it — recovering the boundary speech lost on every window. Healthy windows that
reach ~30s still advance a full stride, so this adds decodes only where speech
was actually being stranded.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import gzip

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's reference fallback schedule and the two health checks it gates on.
_TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
_LOGPROB_THRESHOLD = -1.0           # mean token log-prob below this ⇒ low confidence
_COMPRESSION_RATIO_THRESHOLD = 2.4  # text this compressible ⇒ repetition loop

_WINDOW_S = 30.0
_TIME_PRECISION = 0.02              # seconds per Whisper timestamp-token step
_MIN_ADVANCE_S = 5.0               # floor on the seek so a collapsed window can't
                                   # crawl forward (re-decode cost / overlap guard)


def _compression_ratio(text: str) -> float:
    """gzip compressibility — a repetition loop compresses far above ~2.4."""
    data = text.encode("utf-8")
    if not data:
        return 0.0
    return len(data) / len(gzip.compress(data))


def _decode_window(features, prompt_tokens, tokenizer):
    """Decode one 30s window, escalating temperature until it stops degenerating.

    Returns ``(text, sequence_ids)`` — the raw ids are kept so the caller can read
    the timestamp tokens that ``skip_special_tokens`` strips from ``text``.
    """
    text = ""
    seq_ids: list[int] = []
    for temperature in _TEMPERATURES:
        if temperature == 0.0:
            results = generate(
                features,
                [prompt_tokens],
                beam_size=5,
                sampling_temperature=0.0,
                return_scores=True,
                return_no_speech_prob=True,
            )
        else:
            # Beam search is deterministic, so escalation must switch to
            # temperature sampling (topk=0 ⇒ sample over the full distribution).
            results = generate(
                features,
                [prompt_tokens],
                beam_size=1,
                sampling_topk=0,
                sampling_temperature=temperature,
                return_scores=True,
                return_no_speech_prob=True,
            )
        result = results[0]
        seq_ids = result.sequences_ids[0]
        text = tokenizer.decode(seq_ids, skip_special_tokens=True).strip()
        avg_logprob = result.scores[0]
        if (
            _compression_ratio(text) <= _COMPRESSION_RATIO_THRESHOLD
            and avg_logprob >= _LOGPROB_THRESHOLD
        ):
            break
    return text, seq_ids


def _seek_advance_s(seq_ids, timestamp_begin) -> float:
    """How far (s) to advance the window, from the last segment-end timestamp.

    Timestamp tokens have ids ``>= timestamp_begin`` and encode 0.02s steps. The
    last timestamp token marks where the model's final emitted segment ends; if
    the window stopped short of 30s, seeking there picks the next window up at the
    start of the utterance that was cut. With fewer than two timestamps (or a
    boundary at/near the window edge) there is nothing to recover, so advance a
    full stride.
    """
    ts = [t - timestamp_begin for t in seq_ids if t >= timestamp_begin]
    if len(ts) < 2:
        return _WINDOW_S
    last_end_s = ts[-1] * _TIME_PRECISION
    if last_end_s < _MIN_ADVANCE_S or last_end_s >= _WINDOW_S:
        return _WINDOW_S
    return last_end_s


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    # No <|notimestamps|> ⇒ the decoder emits timestamp tokens we can seek by.
    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = processor.tokenizer.convert_tokens_to_ids("<|0.00|>")

    chunk_samples = int(_WINDOW_S * sr)
    n = len(audio)

    texts = []
    pos = 0
    while pos < n:
        chunk = audio[pos : pos + chunk_samples]
        if len(chunk) == 0:
            break
        features = to_storage_view(
            processor(chunk, sampling_rate=sr, return_tensors="np").input_features
        )
        text, seq_ids = _decode_window(features, prompt_tokens, processor.tokenizer)
        if text:
            texts.append(text)

        advance = int(_seek_advance_s(seq_ids, timestamp_begin) * sr)
        # Never below a full stride on the trailing partial window, and never
        # zero — both would loop or stall.
        if advance <= 0 or len(chunk) < chunk_samples:
            advance = chunk_samples
        pos += advance

    return " ".join(texts)
