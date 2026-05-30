"""Workspace transcribe — autoresearch evolves this file.

Long-form coverage via timestamp-guided sequential windows. Each window is
decoded WITH Whisper timestamp tokens enabled (the SOT prompt omits
``<|notimestamps|>``), so the decoder emits ``<|t.tt|>`` tokens that survive in
``sequences_ids`` (any id at or above the ``<|0.00|>`` token id; each step above
it is 0.02s). The last such timestamp tells us where the decoder believes the
final complete utterance ended inside the window — so the next window seeks to
that point instead of a blind fixed 30s stride. This is the canonical OpenAI
long-form seek: it stops slicing an utterance that straddles a 30s boundary,
which is the boundary-cut that drives the deletion + repeated-text collapse on
this batch. Windows are still conditioned on the previous window's clean text
through the ``<|startofprev|>`` prefix for cross-window coherence.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30
_MAX_PREV_TOKENS = 224  # Whisper prompt context budget
_TS_STEP_SECONDS = 0.02  # Whisper timestamp token resolution
_MIN_ADVANCE_FRACTION = 0.5  # floor seek advance to bound runtime / prevent stall


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tok = processor.tokenizer

    # NOTE: <|notimestamps|> is intentionally omitted so the decoder emits
    # timestamp tokens; they are stripped from the text by skip_special_tokens
    # but read out of the raw sequence to drive the seek.
    sot = tok.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    startofprev = tok.convert_tokens_to_ids("<|startofprev|>")
    timestamp_begin = tok.convert_tokens_to_ids("<|0.00|>")

    chunk_len = _CHUNK_SECONDS * sr
    min_advance = int(_CHUNK_SECONDS * _MIN_ADVANCE_FRACTION * sr)
    n_samples = len(audio)

    texts: list[str] = []
    prev_ids: list[int] = []
    seek = 0
    while seek < n_samples:
        seg = audio[seek : seek + chunk_len]
        if seg.size == 0:
            break

        inputs = processor(seg, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        if prev_ids:
            prompt = [startofprev] + prev_ids[-_MAX_PREV_TOKENS:] + sot
        else:
            prompt = sot

        results = generate(
            features,
            [prompt],
            beam_size=1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text = tok.decode(token_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text)
            # re-encode the clean decoded text as next window's context, so EOT
            # / timestamp ids never leak into the <|startofprev|> prefix
            prev_ids = tok.encode(text.strip(), add_special_tokens=False)

        # find the last timestamp token to decide where the next window starts
        seg_seconds = seg.shape[0] / sr
        last_ts_seconds = None
        for tid in reversed(token_ids):
            if tid >= timestamp_begin:
                last_ts_seconds = (tid - timestamp_begin) * _TS_STEP_SECONDS
                break

        if last_ts_seconds is not None:
            advance = int(min(last_ts_seconds, seg_seconds) * sr)
        else:
            advance = chunk_len
        # a degenerate early timestamp would re-decode most of the window and
        # blow the runtime budget (or stall) — floor the stride.
        advance = max(advance, min_advance)
        seek += advance

    return " ".join(t.strip() for t in texts if t.strip())
