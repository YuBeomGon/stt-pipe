"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing: Whisper's feature extractor pins every clip to a fixed
30-second mel (3000 frames), so the single-window stub discards everything past
0:30. Most 0715 calls run minutes long, so that truncation is the dominant
deletion source. We split the audio into 30-second windows and decode them in
order, stitching the transcripts back together.

The previous attempt concatenated *every* window into one ``[N,128,3000]``
batch and handed it to ``generate`` in a single call; on minutes-long calls
that batch is large enough to exhaust CUDA memory (RuntimeError: out of
memory). We keep the windowing but cap how many windows decode per
``generate`` call so peak memory stays bounded regardless of clip length.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
_BATCH_WINDOWS = 4


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    window_samples = int(sr * _WINDOW_SECONDS)
    if window_samples <= 0:
        window_samples = len(audio) or 1

    starts = list(range(0, max(len(audio), 1), window_samples))

    feature_list = []
    for start in starts:
        chunk = audio[start : start + window_samples]
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        feature_list.append(np.asarray(inputs.input_features))

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    pieces = []
    for batch_start in range(0, len(feature_list), _BATCH_WINDOWS):
        batch = feature_list[batch_start : batch_start + _BATCH_WINDOWS]
        batched = np.concatenate(batch, axis=0)
        features = to_storage_view(batched)
        prompts = [prompt_tokens] * batched.shape[0]

        results = generate(
            features,
            prompts,
            beam_size=5,
            patience=2.0,
            length_penalty=1.1,
            no_repeat_ngram_size=3,
            sampling_temperature=0.0,
        )

        for result in results:
            token_ids = result.sequences_ids[0]
            piece = processor.tokenizer.decode(token_ids, skip_special_tokens=True)
            piece = piece.strip()
            if piece:
                pieces.append(piece)

    return " ".join(pieces)
