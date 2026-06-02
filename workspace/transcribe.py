"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing: Whisper's feature extractor pins every clip to a fixed
30-second mel (3000 frames), so the single-window stub discards everything past
0:30. Most 0715 calls run minutes long, so that truncation is the dominant
deletion source. We split the audio into 30-second windows and decode them in
order, stitching the transcripts back together.

Each window is conditioned on the tail of the previous window's text
(<|startofprev|> + recent token ids before the SOT sequence) so decoding does
not restart cold at every 30s hop.

Memory: peak decoder VRAM scales with (windows-per-call x beam x seq_len). The
prev-text variants OOM'd twice — iter_005 at batch=4, iter_006 at batch=2 —
because the longer prompt enlarges every beam's KV-cache and halving the window
batch alone did not get under the ceiling. We attack *both* memory axes: decode
exactly one window per ``generate`` call (the natural form, since each prev
prefix depends on the prior window's output) and cap the prev-context block, so
peak concurrency is 1 x beam over a short prefix — the smallest footprint used.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
_PREV_CONTEXT_TOKENS = 64


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

    sot_suffix = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )
    startofprev_id = processor.tokenizer.convert_tokens_to_ids("<|startofprev|>")
    eot_id = processor.tokenizer.convert_tokens_to_ids("<|endoftext|>")

    pieces = []
    prev_tokens: list[int] = []
    for window in feature_list:
        features = to_storage_view(window)

        if prev_tokens:
            prompt = [startofprev_id] + prev_tokens[-_PREV_CONTEXT_TOKENS:] + sot_suffix
        else:
            prompt = sot_suffix

        results = generate(
            features,
            [prompt],
            beam_size=5,
            patience=2.0,
            length_penalty=1.1,
            no_repeat_ngram_size=3,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text_tokens = [t for t in token_ids if t < eot_id]
        if text_tokens:
            prev_tokens = text_tokens[-_PREV_CONTEXT_TOKENS:]

        piece = processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if piece:
            pieces.append(piece)

    return " ".join(pieces)
