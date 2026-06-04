"""Workspace transcribe — autoresearch evolves this file.

Discovery (iter 5, explore): generate() exposes two return-value channels the
pipeline has never read — ``results[0].scores`` (the length-normalized average
log-prob of the decode, surfaced only when ``return_scores=True``) and
``results[0].no_speech_prob`` (surfaced by ``return_no_speech_prob=True``).
iter_004 accepts every greedy decode unconditionally, so a window where the
decoder collapses (early EOS, drift, repetition) is kept as-is and its dropped
span becomes pure deletion — the dominant axis (del 67%, length_ratio 0.82).

Mechanism: Whisper's native robustness loop is *temperature fallback*. Decode
greedy first and read ``scores[0]`` as the decode's average log-prob; if it
falls below the standard ``-1.0`` confidence gate the greedy pass is judged
failed, so re-decode the *same* window at rising sampling temperatures (CT2
samples from the full distribution when ``sampling_topk=0``) and keep the
highest-confidence candidate. A collapsed greedy pass that dropped half its
window is then replaced by a sampled pass that covers it, recovering deleted
span. ``no_speech_prob`` guards the loop: a window the model flags as silence
(>= 0.6) is accepted immediately rather than re-rolled at high temperature,
which on non-speech would only invite hallucination.

The timestamp-driven seek from iter_004 is retained for window advancement.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
# CT2 emits a timestamp token every 0.02 s of audio (Whisper's frame stride).
_TIMESTAMP_RESOLUTION = 0.02
# Only trust a last-timestamp advance inside this range; outside it (early
# collapse, or no timestamp) we consume the whole window.
_MIN_ADVANCE_SECONDS = 20.0

# Temperature-fallback schedule (Whisper's native robustness loop). Greedy
# first; climb only when a decode is judged failed. Capped at 4 rungs so the
# worst-case per-window cost stays bounded against the runtime budget.
_TEMPERATURES = (0.0, 0.2, 0.4, 0.6)
# Standard Whisper avg-log-prob gate: at or above this the decode is trusted.
_LOGPROB_THRESHOLD = -1.0
# A window the model is this confident is silence is not worth re-rolling.
_NO_SPEECH_THRESHOLD = 0.6


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio).reshape(-1)
    window = _WINDOW_SECONDS * sr

    # No <|notimestamps|>: keep the decoder in timestamp-emitting mode.
    prompt_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    texts = []
    seek = 0
    n = len(audio)
    while seek < n:
        chunk = audio[seek : seek + window]

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        # Temperature fallback: keep the highest avg-log-prob decode, stopping
        # as soon as one clears the confidence gate (or the window reads as
        # silence). scores[0] and no_speech_prob are the unused return channels.
        best = None
        best_logprob = float("-inf")
        for temp in _TEMPERATURES:
            kwargs = {
                "beam_size": 1,
                "return_scores": True,
                "return_no_speech_prob": True,
            }
            if temp > 0.0:
                # sampling_topk=0 makes CT2 sample the full distribution so the
                # temperature actually perturbs the decode (topk=1 is greedy).
                kwargs["sampling_topk"] = 0
                kwargs["sampling_temperature"] = temp
            else:
                kwargs["sampling_temperature"] = 0.0

            result = generate(features, [prompt_tokens], **kwargs)[0]
            avg_logprob = result.scores[0]
            if avg_logprob > best_logprob:
                best, best_logprob = result, avg_logprob
            if (
                avg_logprob >= _LOGPROB_THRESHOLD
                or result.no_speech_prob >= _NO_SPEECH_THRESHOLD
            ):
                break

        token_ids = best.sequences_ids[0]

        # skip_special_tokens strips the emitted timestamp tokens from the text.
        text = tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if text:
            texts.append(text)

        # Advance the seek to the decoder's last reliable timestamp. Only trust
        # it on a full 30 s window inside the sane range; otherwise consume the
        # whole window (final short chunk, early collapse, or no timestamp).
        timestamps = [t - timestamp_begin for t in token_ids if t >= timestamp_begin]
        advance_s = float(_WINDOW_SECONDS)
        if len(chunk) >= window and timestamps and timestamps[-1] > 0:
            last_ts = timestamps[-1] * _TIMESTAMP_RESOLUTION
            if _MIN_ADVANCE_SECONDS <= last_ts <= _WINDOW_SECONDS:
                advance_s = last_ts

        seek += max(int(advance_s * sr), 1)

    return " ".join(texts)
