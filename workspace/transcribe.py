"""Workspace transcribe — autoresearch evolves this file.

Timestamp-guided sliding window: instead of slicing the audio at blind 30 s
boundaries (which cut words/utterances mid-stream and trigger boundary
deletions + repetition collapse), we let the decoder emit timestamp tokens and
advance the window cursor by the *actually-consumed* span. This is Whisper's
native long-form algorithm and it is content-adaptive — each window ends on a
decoder-chosen boundary, so the next window resumes cleanly instead of in the
middle of a token.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's receptive field is a fixed 30 s mel window.
_WINDOW_SECONDS = 30.0
# Each timestamp token quantises time in 0.02 s steps (`<|0.00|>`..`<|30.00|>`).
_TIME_PRECISION = 0.02
# If the decoder's last timestamp advances less than this, treat the window as
# untrustworthy and fall back to a full-window jump so the cursor never stalls.
_MIN_ADVANCE_SECONDS = 2.0


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    n_samples = audio.shape[0]
    win_samples = int(_WINDOW_SECONDS * sr)

    # Timestamp tokens occupy the top of the vocab, starting at `<|0.00|>`.
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    prompt_tokens = tokenizer.convert_tokens_to_ids(
        # Note: NO <|notimestamps|> — we want the timestamp channel back.
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )

    pieces: list[str] = []
    cursor = 0
    while cursor < n_samples:
        chunk = audio[cursor : cursor + win_samples]
        chunk_seconds = chunk.shape[0] / sr

        inputs = processor(
            [chunk],
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        res = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )[0]
        token_ids = res.sequences_ids[0]

        # Split the decoder output into text tokens and timestamp tokens.
        text_tokens = [t for t in token_ids if t < timestamp_begin]
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]

        text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

        # Advance the cursor by the span the decoder actually consumed: the
        # last emitted timestamp. If it gives no usable boundary, jump a full
        # window so we never loop on the same audio.
        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
