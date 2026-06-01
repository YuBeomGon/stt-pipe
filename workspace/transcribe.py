"""Workspace transcribe — autoresearch evolves this file.

Timestamp-decoding (iter_004) lifted coverage to length_ratio 0.74, but that
number is suspiciously specific: if large-v3 reliably EOTs after transcribing
only ~22s of each 30s window, a *fixed* 30s stride skips the un-transcribed
~8s tail of every window (22/30 ≈ 0.73). The early-EOT does not vanish in
timestamp mode — it just becomes observable, because the decoder stamps where
it actually stopped.

This iteration surfaces the timestamp tokens as a **seek-feedback signal**
(prior iters decoded their offsets only to filter padding). Instead of a fixed
30s stride we advance the window to the END of the last emitted segment — the
standard long-form Whisper seek. The tail the decoder never reached is then
re-read at the head of the next window instead of being skipped, so the
structural deletion that fixed striding bakes in is recovered. A 20s advance
floor bounds the per-file window count (and runtime) when a window stamps an
unusually early stop.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
# Floor on how far each window advances. Without it a window that stamps a very
# early stop would advance only a few seconds and multiply the decode-call
# count (runtime). 20s caps the worst-case window growth at 1.5x while leaving
# the typical ~22-29s last-segment advance untouched.
_MIN_ADVANCE_SECONDS = 20


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio).reshape(-1)
    window = int(_WINDOW_SECONDS * sr)
    min_advance = int(_MIN_ADVANCE_SECONDS * sr)
    n = len(audio)

    # Timestamp-decoding prompt: NO <|notimestamps|>, so the decoder emits
    # <|t|> boundaries; their offsets drive the adaptive stride below.
    prompt_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    # Every token id >= <|0.00|> is a timestamp token. offset_s = (id - begin)*0.02.
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    texts = []
    seek = 0
    while seek < n:
        chunk = audio[seek : seek + window]
        if len(chunk) == 0:
            break
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        # input_features is (1, n_mels, n_frames); the extractor pads the final
        # short chunk up to the full 30s window for us.
        features = to_storage_view(inputs.input_features)

        # Greedy (beam_size=1) leaves length_penalty inert and lets the decoder
        # fall into repetition loops that collapse to apparent deletion. Beam
        # search activates length_penalty>1, which rewards longer hypotheses —
        # aimed straight at the dominant deletion/coverage axis — and the beam
        # margin suppresses the greedy repeat loop (the repeated_text file).
        # beam_size=3 (not 5) keeps ~3x decode within the 719.9s budget.
        results = generate(
            features,
            [prompt_tokens],
            beam_size=3,
            length_penalty=1.1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text_ids = [t for t in token_ids if t < timestamp_begin]
        texts.append(tokenizer.decode(text_ids, skip_special_tokens=True))

        # Final partial window: extractor padded it, nothing follows — stop.
        if len(chunk) < window:
            break

        # Advance to the end of the last emitted segment (last timestamp token).
        # If the window emitted no timestamp, fall back to a full-window stride.
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]
        if ts_tokens:
            advance = int((ts_tokens[-1] - timestamp_begin) * 0.02 * sr)
            advance = min(max(advance, min_advance), window)
        else:
            advance = window
        seek += advance

    return " ".join(t.strip() for t in texts if t.strip())
