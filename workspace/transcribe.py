"""Workspace transcribe — autoresearch evolves this file.

Coverage stayed low (length_ratio ≈ 0.66, deletion ≈ 82%) even though every
second of audio is fed to the model — iter3 localized the loss to *inside* the
30s windows: each window decodes but stops part-way through, so the tail of the
window is silently dropped as deletion.

Every iteration so far hard-coded ``<|notimestamps|>`` into the decoder prompt,
so the decode has NEVER run in Whisper's timestamped regime. notimestamps
decode is prone to early-EOS on long dense segments; the timestamp-trained
decode is built to walk segment-by-segment to the window end, which directly
attacks the in-window deletion. Timestamps also tell us *where* the model
stopped, so instead of a blind 30s hop we advance the cursor to the last
emitted timestamp — a window that truncated early gets its dropped tail
re-decoded with fresh context rather than lost. This is the standard sequential
long-form algorithm, only possible once timestamps are enabled.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
# Whisper timestamp tokens are spaced 0.02s apart starting at <|0.00|>.
_TS_STEP = 0.02
# If the last timestamp lands before this, treat the window as having no usable
# continuation point and hop a full window — bounds total window count and
# guarantees the cursor always advances (loop termination).
_MIN_ADVANCE = 5.0


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    window = _WINDOW_SECONDS * sr
    n = len(audio)

    # Prompt WITHOUT <|notimestamps|> -> the decoder emits timestamp tokens.
    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    # Any token id >= this is a timestamp token (<|0.00|> .. <|30.00|>);
    # everything below (text, eos, control) is not.
    ts_begin = processor.tokenizer.convert_tokens_to_ids("<|0.00|>")

    segments = []
    pos = 0
    while pos < n:
        chunk = audio[pos : pos + window]
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
            no_repeat_ngram_size=3,
        )[0]
        token_ids = result.sequences_ids[0]

        text = processor.tokenizer.decode(
            token_ids, skip_special_tokens=True
        ).strip()
        if text:
            segments.append(text)

        # Advance the cursor to the last timestamp the model actually reached,
        # so an early-truncated window re-decodes its dropped tail next pass.
        last_ts = None
        for tid in reversed(token_ids):
            if tid >= ts_begin:
                last_ts = (tid - ts_begin) * _TS_STEP
                break

        if last_ts is not None and last_ts >= _MIN_ADVANCE:
            advance = min(last_ts, _WINDOW_SECONDS)
        else:
            advance = _WINDOW_SECONDS
        pos += int(advance * sr)

    return " ".join(segments)
