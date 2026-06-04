"""Workspace transcribe — autoresearch evolves this file.

Timestamp-guided long-form decoding. The previous pipelines decided window
boundaries *before* the model ever ran — first blind 30s seams, then
frame-energy VAD. Both guess where speech ends from the waveform alone, and
both leave the dominant error on the coverage/deletion axis (del 68%,
length_ratio 0.80): a guessed boundary that lands a little early silently drops
the span after it.

Here the boundaries come from the model instead of from the audio. The decoder
runs with timestamps ENABLED (the prompt omits ``<|notimestamps|>``), so each
30s window's output carries Whisper's own timestamp tokens — a return channel
the prior pipelines threw away (they forced ``<|notimestamps|>`` and stripped
specials). The token id of a timestamp token maps linearly to a time offset
(``(id - timestamp_begin) * 0.02s``). We read the *last* timestamp the model
emitted in a window and advance ``seek`` to exactly that point, so the next
window starts where speech actually left off. Text is emitted only up to that
last timestamp (the last complete segment), so the re-read region is never
double-counted.

Peak GPU memory is still a single 30s window (one ``generate`` per seek), so
the long-recording OOM stays fixed.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30.0
_TIME_PRECISION = 0.02  # Whisper timestamp token resolution, seconds per step.
_MIN_ADVANCE_SECONDS = 1.0  # below this the timestamp is untrustworthy.


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    # Timestamps ON: omit <|notimestamps|>. The decoder now interleaves
    # <|t|> tokens between segments — that is the channel we steer seek with.
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

        results = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )
        token_ids = results[0].sequences_ids[0]

        # Locate the model's emitted timestamps. The last one bounds the last
        # complete segment: emit text up to it, seek to it. With >=2 timestamps
        # we trust the boundary; otherwise fall back to a full-window advance
        # so a degenerate window can never stall the seek loop.
        ts_positions = [i for i, t in enumerate(token_ids) if t >= timestamp_begin]
        advance = chunk_len
        emit_ids = token_ids
        if len(ts_positions) >= 2:
            last_i = ts_positions[-1]
            last_ts_steps = token_ids[last_i] - timestamp_begin
            ts_advance = int(last_ts_steps * _TIME_PRECISION * sr)
            if ts_advance >= min_advance:
                advance = ts_advance
                emit_ids = token_ids[:last_i]

        text = processor.tokenizer.decode(emit_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text.strip())

        seek += advance

    return " ".join(texts)
