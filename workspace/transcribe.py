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
            # Coverage/deletion is the dominant axis (length_ratio ~0.65, del
            # 81%) and it is uniform across all files, not just looping ones:
            # under greedy decode the model loops then emits EOS early and drops
            # the window tail. beam_size>1 escapes that greedy loop-then-EOS
            # trap, and length_penalty>1 favors longer hypotheses, recovering
            # the deleted tail directly. Both knobs were inert under the prior
            # greedy config. Beam search now carries repetition control, so the
            # inherited n-gram block can stay narrow without taxing coverage.
            beam_size=2,
            length_penalty=1.2,
            sampling_temperature=0.0,
            no_repeat_ngram_size=5,
        )

        for r in results:
            piece = processor.tokenizer.decode(
                r.sequences_ids[0], skip_special_tokens=True
            ).strip()
            if piece:
                pieces.append(piece)

    return " ".join(pieces)
