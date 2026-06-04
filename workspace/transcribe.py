"""Workspace transcribe — autoresearch evolves this file.

Temperature-fallback long-form decoding — the OpenAI Whisper reference
robustness loop, which this job has never actually run. Every prior pipeline
decoded each 30s window exactly ONCE, greedily (``beam_size=1``,
``sampling_temperature=0.0``), and committed to whatever token path that single
pass produced. So any window where greedy locked onto a wrong path stayed
wrong — which is exactly the substitution-dominant error profile (sub 57% with
length_ratio 0.94 healthy: the model emits roughly the right length but the
wrong characters, the signature of a bad greedy commit that nothing re-examines).

This pipeline decodes each window against an escalating temperature ladder
behind a quality gate built from the backend's return channels: ``return_scores``
(iter_006's discovery) gives the hypothesis avg log-prob, and a zlib
compression ratio over the decoded text catches repetition collapse. A window
is accepted at the *first* temperature whose decode clears both the avg-logprob
and compression-ratio thresholds; a window that keeps failing — a
low-confidence substitution path, or a repetition loop (high compression
ratio) — is re-decoded at a higher temperature (sampling from the full
distribution), and if nothing clears the gate the highest-logprob attempt is
kept. This is the standard mechanism for escaping a bad greedy commit and is
the one robustness loop the pipeline never had; clean windows still pass at
t=0.0 on the first decode, so the extra compute lands only on the windows that
need it.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import zlib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30.0
_TIME_PRECISION = 0.02  # Whisper timestamp token resolution, seconds per step.
_MIN_ADVANCE_SECONDS = 1.0  # below this the last-timestamp boundary is untrustworthy.

# OpenAI Whisper reference fallback ladder + acceptance gates. A window's decode
# is accepted at the first temperature clearing BOTH gates; failures escalate.
_TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
_LOGPROB_THRESHOLD = -1.0
_COMPRESSION_RATIO_THRESHOLD = 2.4


def _compression_ratio(text: str) -> float:
    """zlib compression ratio of the decoded text — high => repetition loop."""
    data = text.encode("utf-8")
    if not data:
        return 0.0
    return len(data) / len(zlib.compress(data))


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    # Timestamps ON (omit <|notimestamps|>): the interleaved <|t|> tokens are
    # the long-form seek channel, and survive sampled decodes too.
    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = processor.tokenizer.convert_tokens_to_ids("<|0.00|>")

    audio = np.asarray(audio).reshape(-1)
    n = audio.shape[0]
    chunk_len = int(_CHUNK_SECONDS * sr)
    min_advance = int(_MIN_ADVANCE_SECONDS * sr)

    texts: list[str] = []
    seek = 0
    while seek < n:
        chunk = audio[seek : seek + chunk_len]
        if chunk.shape[0] == 0:
            break
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        # Temperature-fallback decode: accept the first gate-passing attempt,
        # else fall back to the highest-logprob attempt seen.
        chosen_ids = None
        best_logprob = None
        best_ids = None
        for temp in _TEMPERATURES:
            if temp == 0.0:
                results = generate(
                    features,
                    [prompt_tokens],
                    beam_size=1,
                    sampling_temperature=0.0,
                    return_scores=True,
                )
            else:
                # sampling_topk=0 => sample from the full distribution so the
                # temperature actually perturbs the path (topk=1 would stay greedy).
                results = generate(
                    features,
                    [prompt_tokens],
                    beam_size=1,
                    sampling_topk=0,
                    sampling_temperature=temp,
                    return_scores=True,
                )
            res = results[0]
            token_ids = res.sequences_ids[0]
            avg_logprob = float(res.scores[0])
            text = processor.tokenizer.decode(token_ids, skip_special_tokens=True)
            cr = _compression_ratio(text)

            if best_logprob is None or avg_logprob > best_logprob:
                best_logprob = avg_logprob
                best_ids = token_ids

            if (
                avg_logprob >= _LOGPROB_THRESHOLD
                and cr <= _COMPRESSION_RATIO_THRESHOLD
            ):
                chosen_ids = token_ids
                break

        token_ids = chosen_ids if chosen_ids is not None else best_ids

        # Long-form seek: advance to the last timestamp the window emitted and
        # emit text up to it; fall back to the full chunk on a degenerate window.
        ts_positions = [i for i, t in enumerate(token_ids) if t >= timestamp_begin]
        advance = chunk_len
        emit_ids = token_ids
        if len(ts_positions) >= 2:
            last_i = ts_positions[-1]
            last_advance = int(
                (token_ids[last_i] - timestamp_begin) * _TIME_PRECISION * sr
            )
            if last_advance >= min_advance:
                advance = last_advance
                emit_ids = token_ids[:last_i]

        text = processor.tokenizer.decode(emit_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text.strip())

        seek += advance

    return " ".join(texts)
