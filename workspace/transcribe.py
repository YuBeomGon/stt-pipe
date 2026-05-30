"""Workspace transcribe — autoresearch evolves this file.

Long-form coverage via sequential 30s windows, each window conditioned on the
previous window's decoded text through the backend's ``<|startofprev|>`` prompt
prefix. This is the Whisper condition-on-previous-text mechanism: the
``prompts`` argument is not limited to a bare start-of-transcript header — it
accepts a leading ``<|startofprev|>`` segment carrying prior context tokens,
which the decoder attends to for cross-window coherence.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30
_MAX_PREV_TOKENS = 224  # Whisper prompt context budget


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tok = processor.tokenizer

    sot = tok.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )
    startofprev = tok.convert_tokens_to_ids("<|startofprev|>")

    chunk_len = _CHUNK_SECONDS * sr
    n_chunks = max(1, int(np.ceil(len(audio) / chunk_len)))

    texts: list[str] = []
    prev_ids: list[int] = []
    for i in range(n_chunks):
        seg = audio[i * chunk_len : (i + 1) * chunk_len]
        if seg.size == 0:
            continue

        inputs = processor(seg, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        if prev_ids:
            prompt = [startofprev] + prev_ids[-_MAX_PREV_TOKENS:] + sot
        else:
            prompt = sot

        results = generate(
            features,
            [prompt],
            beam_size=1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text = tok.decode(token_ids, skip_special_tokens=True)
        texts.append(text)

        # re-encode the clean decoded text as next window's context, so EOT /
        # other special ids never leak into the <|startofprev|> prefix
        if text.strip():
            prev_ids = tok.encode(text.strip(), add_special_tokens=False)

    return " ".join(t.strip() for t in texts if t.strip())
