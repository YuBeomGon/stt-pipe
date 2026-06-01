"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing (sequential 30s) recovers the audio past 0:30 that a single
30s feature window would drop. But every prior iteration decoded each window in
``<|notimestamps|>`` mode, and on dense multi-utterance audio large-v3 Whisper
reliably emits EOT *early* in that mode — it transcribes the front of the 30s
window and stops, so each window is only partially covered. That is the exact
signature of the dominant failure here (length_ratio 0.65, deletion 80%).

This iteration surfaces the unused **timestamp-decoding** path: dropping
``<|notimestamps|>`` from the prompt puts the decoder in the regime it was
trained for, where it emits ``<|t|>`` segment-boundary tokens and keeps
transcribing to the window end instead of terminating early. We strip those
timestamp tokens back out before decoding to text. Window stride stays a fixed
30s so the per-call count (and runtime budget) matches the kept best.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio).reshape(-1)
    window = int(_WINDOW_SECONDS * sr)
    n_windows = max(1, int(np.ceil(len(audio) / window)))

    # Timestamp-decoding prompt: NO <|notimestamps|>, so the decoder emits
    # <|t|> boundaries and transcribes the whole window instead of stopping
    # early at the first EOT (the early-EOT collapse is the coverage loss).
    prompt_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    # Every token id >= <|0.00|> is a timestamp token; strip them before the
    # text decode so the predicted boundaries don't leak into the transcript.
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    texts = []
    for i in range(n_windows):
        chunk = audio[i * window : (i + 1) * window]
        if len(chunk) == 0:
            continue
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        # input_features is (1, n_mels, n_frames); the extractor pads the final
        # short chunk up to the full 30s window for us.
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text_ids = [t for t in token_ids if t < timestamp_begin]
        texts.append(tokenizer.decode(text_ids, skip_special_tokens=True))

    return " ".join(t.strip() for t in texts if t.strip())
