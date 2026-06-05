"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing pipeline: Whisper's feature extractor pads/truncates to a
single 30-second mel window, so feeding a multi-minute call in one shot drops
everything past 0:30. We slice the waveform into consecutive 30s windows and
decode the per-window transcripts, then concatenate. To stay within GPU memory
(a several-minute call yields ~10+ windows; decoding all of them in one batched
``generate`` call exhausts CUDA memory), windows are decoded in small fixed-size
batches rather than one giant batch.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's mel front-end is fixed at 30 seconds; one chunk == one window.
_WINDOW_SECONDS = 30
# Cap concurrent windows per generate() call so GPU memory stays bounded
# regardless of call length (the all-at-once batch OOM'd on long calls).
_MAX_WINDOWS_PER_BATCH = 4


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    audio = np.asarray(audio).reshape(-1)
    window = _WINDOW_SECONDS * sr

    # Consecutive non-overlapping 30s windows. The feature extractor zero-pads
    # the final short window up to 30s on its own.
    chunks = [audio[start : start + window] for start in range(0, len(audio), window)]
    if not chunks:
        chunks = [audio]

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    pieces: list[str] = []
    for batch_start in range(0, len(chunks), _MAX_WINDOWS_PER_BATCH):
        batch_chunks = chunks[batch_start : batch_start + _MAX_WINDOWS_PER_BATCH]
        feats = [
            processor(chunk, sampling_rate=sr, return_tensors="np").input_features[0]
            for chunk in batch_chunks
        ]
        batch = to_storage_view(np.stack(feats, axis=0))

        results = generate(
            batch,
            [prompt_tokens] * len(batch_chunks),
            beam_size=1,
            sampling_temperature=0.0,
            # Anti-loop block tuned for the dominant deletion/coverage axis.
            # The parent's no_repeat_ngram_size=3 forbids re-emitting ANY exact
            # 3-token sequence, but conversational Korean recurs short trigrams
            # (back-channel/honorific patterns, e.g. "네 네 네"), so n=3 also
            # blocks legitimate speech, knocks the greedy decoder off-track, and
            # deepened deletion (length_ratio 0.65->0.62, cer 0.4128->0.4318 vs
            # plain greedy). The genuine repetition-collapse loop phrases on the
            # repeated_text files span >=4 subword tokens, so widening the block
            # to n=4 still catches the real loop while sparing natural short
            # repeats — relieving the coverage penalty on the clean files.
            no_repeat_ngram_size=4,
        )

        for r in results:
            piece = processor.tokenizer.decode(
                r.sequences_ids[0], skip_special_tokens=True
            ).strip()
            if piece:
                pieces.append(piece)

    return " ".join(pieces)
