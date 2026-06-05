"""Workspace transcribe — previous-text-conditioned sliding window.

DIVERGES from the timestamp-only pipeline by feeding the running transcript
back into the decoder as a context prompt. The incumbent decoded each 30 s
window in isolation: the model saw audio but never its own prior output, so
domain terminology (insurance call-center jargon, repeated names, policy and
product terms) was re-guessed window-by-window with no continuity — the
substitution failure mode that now dominates (sub 54 %, length_ratio 0.95, so
coverage is NOT the problem; *what* is recognized is the headroom).

Whisper has a dedicated prompt channel for exactly this: the <|startofprev|>
token. Prefixing the decode prompt with <|startofprev|> + the previous
window's text tokens conditions the model on what was just said, biasing it
toward contextually-consistent recognition across the window boundary —
precisely the lever for the substitution axis. This is the backend input we
have never conditioned on: the `prompts` argument has only ever carried the
fixed SOT / language / task triple, never prior output.

Coverage is preserved with the established timestamp-steered seek (ledger
iter_003): timestamps stay enabled and the read head still advances by the
model's own last predicted timestamp, so the conditioning is bolted onto a
windowing scheme already proven to keep length_ratio ≈ 0.95.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
_TIME_PRECISION = 0.02  # seconds per timestamp-token step (Whisper convention)
# Cap the carried context. Whisper conditions on up to ~half its text context
# (~224 tokens); keep a margin so the prompt never crowds out the decode.
_MAX_PROMPT_TEXT_TOKENS = 200


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio).reshape(-1)
    if audio.shape[0] == 0:
        return ""

    sot = tokenizer.convert_tokens_to_ids("<|startoftranscript|>")
    lang = tokenizer.convert_tokens_to_ids(_LANGUAGE_TOKEN)
    task = tokenizer.convert_tokens_to_ids(_TASK_TOKEN)
    sot_prev = tokenizer.convert_tokens_to_ids("<|startofprev|>")
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    # No <|notimestamps|>: the model emits timestamp tokens we steer seek on.
    decode_head = [sot, lang, task]

    window = _WINDOW_SECONDS * sr
    n = audio.shape[0]
    seek = 0
    pieces = []
    prev_text_ids: list[int] = []  # text tokens of the previous decoded window

    while seek < n:
        chunk = audio[seek : seek + window]
        chunk_seconds = chunk.shape[0] / sr
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        # Condition on the running transcript via the <|startofprev|> channel.
        if prev_text_ids:
            prompt = [
                sot_prev,
                *prev_text_ids[-_MAX_PROMPT_TEXT_TOKENS:],
                *decode_head,
            ]
        else:
            prompt = list(decode_head)

        results = generate(
            features,
            [prompt],
            beam_size=1,
            sampling_temperature=0.0,
        )
        token_ids = results[0].sequences_ids[0]

        # Text: linguistic tokens sit below the timestamp block at the top of
        # the vocab; drop timestamp + special tokens for the emitted string.
        text_ids = [t for t in token_ids if t < timestamp_begin]
        text = tokenizer.decode(text_ids, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)
            # Re-encode the clean text so no special tokens leak into the
            # context prompt that conditions the next window.
            prev_text_ids = tokenizer.encode(text, add_special_tokens=False)

        # Advance by the model's own last predicted timestamp within this window.
        timestamps = [t for t in token_ids if t >= timestamp_begin]
        advance = window
        if timestamps:
            last_time = (timestamps[-1] - timestamp_begin) * _TIME_PRECISION
            if 0.0 < last_time <= chunk_seconds:
                advance = int(round(last_time * sr))

        # Guarantee forward progress so a degenerate (near-zero) timestamp
        # cannot stall the read head; fall back to a full stride.
        if advance < sr:
            advance = window
        seek += advance

    return " ".join(pieces)
