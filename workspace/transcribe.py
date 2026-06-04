"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing recovers the call tail (the stub decoded only the first
30s), and per-window sequential decode bounds GPU memory. But coverage stayed
low (length_ratio ≈ 0.65, deletion ≈ 80%) even though every second of audio is
fed to the model — so the loss is *inside* the windows, not at the chunker.
These call-center recordings have long low-RMS / noisy spans; on those a single
greedy 30s decode collapses into a repetition loop or stops early
(repeated_text flagged on 5/12 files), and the dropped real content shows up as
deletion.

This iteration conditions on a decode-result signal we previously discarded.
``generate`` returns per-sequence ``scores`` and a ``no_speech_prob`` *only*
when asked via ``return_scores`` / ``return_no_speech_prob`` — flags the
pipeline never set, so those quality signals were thrown away. We greedy-decode
each window, then for any window whose output is empty, low-confidence, or
visibly repetitive — yet whose ``no_speech_prob`` says speech is present — we
re-decode once with beam search + ``no_repeat_ngram_size`` to break the loop
and recover the dropped span. This is a genuine fallback policy, not a silent
error swallow: windows that decode cleanly are untouched.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
# faster-whisper defaults: avg log-prob below this == low confidence; a window
# with no_speech_prob above this is genuine silence and not worth a retry.
_LOW_SCORE = -1.0
_NO_SPEECH = 0.6


def _is_repetitive(token_ids) -> bool:
    """A collapsed greedy decode cycles a few tokens, so the unique-token
    fraction drops sharply. Cheap proxy for the repeated_text failure."""
    if len(token_ids) >= 12:
        return len(set(token_ids)) / len(token_ids) < 0.45
    return False


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    window = _WINDOW_SECONDS * sr
    n_windows = max(1, int(np.ceil(len(audio) / window)))
    chunks = [audio[i * window : (i + 1) * window] for i in range(n_windows)]

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    segments = []
    for chunk in chunks:
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        result = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
            return_scores=True,
            return_no_speech_prob=True,
        )[0]
        token_ids = result.sequences_ids[0]
        score = result.scores[0] if result.scores else 0.0
        no_speech_prob = result.no_speech_prob

        collapsed = not token_ids or score < _LOW_SCORE or _is_repetitive(token_ids)
        if collapsed and no_speech_prob < _NO_SPEECH:
            retry = generate(
                features,
                [prompt_tokens],
                beam_size=5,
                no_repeat_ngram_size=3,
                length_penalty=1.0,
                return_scores=True,
            )[0]
            retry_ids = retry.sequences_ids[0]
            if retry_ids and not _is_repetitive(retry_ids):
                token_ids = retry_ids

        segments.append(
            processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        )

    return " ".join(s for s in segments if s)
