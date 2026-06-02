"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing: Whisper's feature extractor pins every clip to a fixed
30-second mel (3000 frames), so the single-window stub discards everything past
0:30. Most 0715 calls run minutes long, so that truncation is the dominant
deletion source. We split the audio into windows and decode them in order,
stitching the transcripts back together.

TIMESTAMP-DRIVEN SEEKING (new mechanism). The deletion/coverage axis is stuck
(del 64%, length_ratio 0.80) under every fixed-hop scheme tried — a hard 30s
stride (iter_007) and a 4s overlap stride (iter_008) both leave the same span
on the floor. The reason is that the decoder does not transcribe the full 30s
of every window: on long calls it routinely emits <|endoftext|> partway through
the mel (early termination), so a fixed hop that assumes the whole window was
covered steps *past* speech the decoder never committed to — that un-decoded
tail is the residual deletion.

iter_004 established that omitting <|notimestamps|> from the SOT prompt makes
the decoder interleave <|t.tt|> segment-timestamp tokens (id >= the <|0.00|>
token id, each encoding time = (id - ts_begin) * 0.02 s relative to the window
start) into sequences_ids. We now USE that output for the first time: instead
of a fixed stride we advance the next window's start to the LAST emitted
segment-end timestamp. The decoder itself tells us how far it reliably got;
everything past that boundary is re-seeked by the next window rather than
deleted. This is the reference Whisper long-form algorithm, reachable through
the frozen surface with no new import.

We keep family_003's prev-text conditioning (<|startofprev|> + recent token ids
before the SOT sequence — held hallucination at 0.18) and family_001's beam
surface. The re-decoded tail overlap is removed by the same word-level dedup as
iter_008 so recovered coverage does not turn into insertions.

QUALITY FALLBACK (new mechanism, this iter). Corpus-wide the dominant axis is
substitution, but the worst-CER files — the harness focus_files — are not
substitution-bound: they carry ``repeated_text=True`` and length_ratio ~0.81.
Their failure is a beam-search repetition loop: at temperature 0 the decoder
locks onto a degenerate cycle for a window and we currently commit that loop
verbatim, and it also poisons the prev-text prompt of every later window. The
backend exposes a signal we have never read: ``return_scores=True`` populates
``result.scores`` with the per-hypothesis length-normalized average log-prob.
Combined with the gzip compression ratio of the decoded text (the standard
Whisper repetition signal — high ratio = repeated n-grams), this gives a
per-window quality gate. When a window is degenerate (compression ratio too
high OR avg log-prob too low) we re-decode it down a temperature ladder with
sampling, which breaks the deterministic beam loop, and keep the first
acceptable decode (else the least-repetitive of the ladder). This is the
reference Whisper temperature-fallback loop, reachable through the frozen
surface with no new import. Most windows pass the gate on the first (beam,
T=0) decode, so the extra cost is confined to the degenerate windows on the
repeated_text files — well inside the 719s budget.

Memory/runtime: still exactly one window per ``generate`` call ([1,128,3000],
concurrency 1 x beam — the smallest footprint used). A floor on the per-window
advance (``_MIN_ADVANCE_SECONDS``) bounds the window count when a timestamp is
degenerate or missing, keeping runtime inside the 719s budget.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import zlib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
_TIMESTAMP_RESOLUTION = 0.02
_MIN_ADVANCE_SECONDS = 15.0
_PREV_CONTEXT_TOKENS = 64
_MAX_OVERLAP_WORDS = 24

# Reference Whisper quality-fallback thresholds. A window whose decoded text
# compresses past _COMPRESSION_RATIO_THRESHOLD (repeated n-grams) or whose
# beam score falls below _LOGPROB_THRESHOLD is treated as degenerate and
# re-decoded down the temperature ladder.
_TEMPERATURE_LADDER = (0.0, 0.4, 0.8)
_COMPRESSION_RATIO_THRESHOLD = 2.4
_LOGPROB_THRESHOLD = -1.0


def _compression_ratio(text: str) -> float:
    data = text.encode("utf-8")
    if not data:
        return 0.0
    return len(data) / len(zlib.compress(data))


def _dedup_extend(acc_words: list[str], new_words: list[str]) -> None:
    """Append new_words to acc_words, dropping the largest leading run of
    new_words that exactly re-states the trailing run of acc_words (the
    re-seeked tail region that both windows decode)."""
    max_k = min(len(acc_words), len(new_words), _MAX_OVERLAP_WORDS)
    for k in range(max_k, 0, -1):
        if acc_words[-k:] == new_words[:k]:
            acc_words.extend(new_words[k:])
            return
    acc_words.extend(new_words)


def _decode_window(processor, features, prompt) -> list[int]:
    """Decode one window with the reference Whisper temperature fallback.

    Step T=0 first (beam search — the strongest deterministic decode). If the
    result is degenerate by the compression-ratio / avg-log-prob gate, step the
    temperature ladder with sampling (beam_size=1, sampling_topk=0 = sample from
    the full temperature-scaled distribution) to break the repetition loop. Keep
    the first decode that passes the gate; if none does, keep the one with the
    lowest compression ratio (least repetitive)."""
    best_ids: list[int] = []
    best_ratio = float("inf")
    for temperature in _TEMPERATURE_LADDER:
        if temperature == 0.0:
            results = generate(
                features,
                [prompt],
                beam_size=5,
                patience=2.0,
                length_penalty=1.0,
                sampling_temperature=0.0,
                return_scores=True,
            )
        else:
            results = generate(
                features,
                [prompt],
                beam_size=1,
                sampling_topk=0,
                sampling_temperature=temperature,
                return_scores=True,
            )
        result = results[0]
        token_ids = result.sequences_ids[0]
        text = processor.tokenizer.decode(token_ids, skip_special_tokens=True)
        ratio = _compression_ratio(text)

        scores = getattr(result, "scores", None)
        avg_logprob = scores[0] if scores else None

        if ratio < best_ratio:
            best_ids, best_ratio = token_ids, ratio

        if ratio <= _COMPRESSION_RATIO_THRESHOLD and (
            avg_logprob is None or avg_logprob >= _LOGPROB_THRESHOLD
        ):
            return token_ids

    return best_ids


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    window_samples = int(sr * _WINDOW_SECONDS)
    if window_samples <= 0:
        window_samples = len(audio) or 1
    min_advance_samples = max(int(sr * _MIN_ADVANCE_SECONDS), 1)

    # SOT WITHOUT <|notimestamps|> — we want the decoder to emit segment
    # timestamps so we can read where each window's decode reliably ended.
    sot_suffix = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    startofprev_id = processor.tokenizer.convert_tokens_to_ids("<|startofprev|>")
    eot_id = processor.tokenizer.convert_tokens_to_ids("<|endoftext|>")
    ts_begin_id = processor.tokenizer.convert_tokens_to_ids("<|0.00|>")

    acc_words: list[str] = []
    prev_tokens: list[int] = []

    n = max(len(audio), 1)
    cur = 0
    while cur < n:
        chunk = audio[cur : cur + window_samples]
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(np.asarray(inputs.input_features))

        if prev_tokens:
            prompt = [startofprev_id] + prev_tokens[-_PREV_CONTEXT_TOKENS:] + sot_suffix
        else:
            prompt = sot_suffix

        token_ids = _decode_window(processor, features, prompt)

        # Last emitted segment-end timestamp = how far the decoder reliably got
        # into this window. Everything after it is re-seeked, not deleted.
        last_ts_seconds = None
        for t in token_ids:
            if t >= ts_begin_id:
                last_ts_seconds = (t - ts_begin_id) * _TIMESTAMP_RESOLUTION

        text_tokens = [t for t in token_ids if t < eot_id]
        if text_tokens:
            prev_tokens = text_tokens[-_PREV_CONTEXT_TOKENS:]

        piece = processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if piece:
            _dedup_extend(acc_words, piece.split())

        if last_ts_seconds is not None and last_ts_seconds * sr >= min_advance_samples:
            advance = int(last_ts_seconds * sr)
        else:
            # Degenerate/absent timestamp: take the full window hop so a single
            # bad window cannot stall the seek or explode the window count.
            advance = window_samples
        cur += max(advance, min_advance_samples)

    return " ".join(acc_words)
