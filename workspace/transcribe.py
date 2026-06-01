"""Workspace transcribe — autoresearch evolves this file.

Long-form 0715 calls are decoded one 30s window at a time. The dominant error
is intra-window deletion: a greedy/beam decode that collapses into a repetition
loop or fires EOS early strands the rest of that window's speech (iter_003).
Whisper's native cure is *temperature fallback* — re-decode a window at rising
temperature whenever the decoder's own confidence says the output degenerated.
That needs the two return values the pipeline was discarding.

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


def _compression_ratio(text: str) -> float:
    """gzip compressibility — a repetition loop compresses far above ~2.4."""
    data = text.encode("utf-8")
    if not data:
        return 0.0
    return len(data) / len(gzip.compress(data))


def _decode_window(features, prompt_tokens, tokenizer) -> str:
    """Decode one 30s window, escalating temperature until it stops degenerating.

    Reads ``scores`` (length-normalised log-prob) and the compression ratio of
    the emitted text — exactly the two signals Whisper uses to detect a
    collapsed decode — and only retries windows that fail them, so healthy
    windows still cost a single decode.
    """
    text = ""
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
        text = tokenizer.decode(
            result.sequences_ids[0], skip_special_tokens=True
        ).strip()
        avg_logprob = result.scores[0]
        if (
            _compression_ratio(text) <= _COMPRESSION_RATIO_THRESHOLD
            and avg_logprob >= _LOGPROB_THRESHOLD
        ):
            break
    return text


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    chunk_samples = 30 * sr
    chunks = [
        audio[start : start + chunk_samples]
        for start in range(0, len(audio), chunk_samples)
    ]
    chunks = [c for c in chunks if len(c) > 0]
    if not chunks:
        return ""

    texts = []
    for chunk in chunks:
        features = to_storage_view(
            processor(chunk, sampling_rate=sr, return_tensors="np").input_features
        )
        text = _decode_window(features, prompt_tokens, processor.tokenizer)
        if text:
            texts.append(text)

    return " ".join(texts)
