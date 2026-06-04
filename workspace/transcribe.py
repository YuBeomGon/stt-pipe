"""Workspace transcribe — autoresearch evolves this file.

Discovery (iter 4, explore): every window so far forced ``<|notimestamps|>``
into the prompt and advanced the window by a fixed +30 s. That fixed cut lands
in the *middle* of an utterance on continuous-speech calls (the worst file has
a 233 s unbroken span sliced into ~8 blind windows), so the decoder hits a hard
boundary mid-word, drifts/loops and terminates early — feeding the dominant
deletion axis (length_ratio 0.65, del 80%, repeated_text on most files).

Mechanism: Whisper's native long-form algorithm runs *with* timestamps. Drop
``<|notimestamps|>`` so the CT2 decoder emits timestamp tokens inside
``results[0].sequences_ids[0]`` (token ids ``>= <|0.00|>``, each step = 0.02 s)
— a return-value channel the pipeline currently strips and throws away. The
*last* timestamp the decoder emits marks where its transcription stayed
reliable inside the 30 s window; advancing the next window's seek to that point
(instead of a blind +30 s) means we never cut mid-utterance and the model
re-decodes the uncertain tail with a clean lead-in. A trusted-range guard keeps
runtime bounded: a timestamp implying a tiny or absent advance falls back to a
full-window step, so worst-case window count stays ~1.5× the fixed split.

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
# collapse, or no timestamp) we consume the whole window. The lower bound caps
# overlap re-decode so runtime stays near the fixed-split cost.
_MIN_ADVANCE_SECONDS = 20.0


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio).reshape(-1)
    window = _WINDOW_SECONDS * sr

    # No <|notimestamps|>: put the decoder into timestamp-emitting mode.
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

        results = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]

        # skip_special_tokens strips the emitted timestamp tokens from the text.
        text = tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if text:
            texts.append(text)

        # Advance the seek to the decoder's last reliable timestamp. Only trust
        # it on a full 30 s window and inside the sane range; otherwise consume
        # the whole window (final short chunk, early collapse, or no timestamp).
        timestamps = [t - timestamp_begin for t in token_ids if t >= timestamp_begin]
        advance_s = float(_WINDOW_SECONDS)
        if len(chunk) >= window and timestamps and timestamps[-1] > 0:
            last_ts = timestamps[-1] * _TIMESTAMP_RESOLUTION
            if _MIN_ADVANCE_SECONDS <= last_ts <= _WINDOW_SECONDS:
                advance_s = last_ts

        seek += max(int(advance_s * sr), 1)

    return " ".join(texts)
