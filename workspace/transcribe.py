"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prior iteration — temperature-fallback, MBR, lexicon
rerank, suppress-mask, pre-emphasis, custom windowing — conditioned the decoder
on exactly one fixed prompt: the SOT triple
``[<|startoftranscript|>, <|ko|>, <|transcribe|>]``. The decoder for each 30 s
window therefore started from a blank linguistic slate, with no knowledge of
the domain vocabulary already established earlier in the *same call*. The only
prompt-side iter in the ledger (iter_004) injected a *static* glossary; nothing
has ever carried the conversation's own decoded text forward.

This slot introduces a structurally different mechanism — **cross-window
context conditioning**, OpenAI Whisper's ``condition_on_previous_text`` policy,
which the frozen backend exposes for free because ``generate(features, prompts,
…)`` takes an arbitrary prompt token sequence per window. Whisper's decoder
understands a prompt of the form

    [<|startofprev|>, *previous_window_text_tokens, <|startoftranscript|>,
     <|ko|>, <|transcribe|>]

where the ``<|startofprev|>``-prefixed span is treated as *prior context the
model has already transcribed*, not as audio to re-emit. The decoded text token
ids from the previous window are already in the model's own vocab space (we
strip them straight out of ``sequences_ids`` below timestamp_begin), so they can
be fed back verbatim — no re-tokenisation.

Why this attacks the dominant axis. The error mix is 57% substitution on a
healthy length_ratio (0.96): the headroom is in *what* gets mis-recognised —
domain terms on phone-band audio (보험료, 피보험자, 자동이체, 약관, 주민등록번호).
When a window's high-frequency obstruent cues are attenuated by the 300-3400 Hz
telephony band-pass, the decoder falls back on its generic language prior and
substitutes a phonetically-adjacent non-term. Priming it with the *actual
domain tokens this very conversation already produced* reshapes that prior
toward the call-center register, so a term decoded confidently in an early
window biases every later window to reproduce it consistently instead of
drifting to a neighbour. This is a decoder-prior intervention orthogonal to
every selection/search knob in the ledger — it changes the conditional
distribution itself, before search.

Guard against the known failure mode. ``condition_on_previous_text`` can
propagate a degenerate/looping window forward as a hallucination seed. So the
carry-forward is gated: a window's text is only promoted into the next prompt
if it is non-degenerate (gzip compression ratio below ceiling) — a genuine
context-reset policy, exactly OpenAI's "reset prompt when the decode looks
untrustworthy" rule, not silent error-swallowing.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import zlib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_PREV_TOKEN = "<|startofprev|>"

_WINDOW_SECONDS = 30.0
_TIME_PRECISION = 0.02
_MIN_ADVANCE_SECONDS = 2.0

_BEAM_SIZE = 5

# Gentle token-level repetition penalty — keeps a primed decoder from latching
# onto a carried-forward term and looping it.
_REPETITION_PENALTY = 1.1

# How many of the previous window's text tokens to prepend as context. Whisper's
# decoder context is 448 tokens; the reference long-form policy caps the prompt
# carry at roughly half that so the SOT triple + new audio decode keep room.
_MAX_PROMPT_TOKENS = 200

# Context-reset gate. If the previous window's text compresses harder than this
# (degenerate / repetitive / hallucinated), it is NOT carried forward — priming
# the next window with a bad decode would seed a propagating hallucination.
# Natural speech ~1.3-1.8; degenerate loops >> 2.4.
_COMPRESSION_RATIO_THRESHOLD = 2.4


def _compression_ratio(text: str) -> float:
    if not text:
        return 0.0
    data = text.encode("utf-8")
    return len(data) / len(zlib.compress(data))


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    n_samples = audio.shape[0]
    win_samples = int(_WINDOW_SECONDS * sr)

    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")
    prev_token_id = tokenizer.convert_tokens_to_ids(_PREV_TOKEN)
    sot_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )

    pieces: list[str] = []
    # Token ids of the previous window's emitted text, carried as decoder
    # context. Empty until the first clean window has been decoded.
    prev_text_tokens: list[int] = []
    cursor = 0
    while cursor < n_samples:
        chunk = audio[cursor : cursor + win_samples]
        chunk_seconds = chunk.shape[0] / sr

        inputs = processor(
            [chunk],
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        # Build the per-window prompt. With established context, prepend the
        # <|startofprev|> span so the decoder conditions on the conversation's
        # own domain vocabulary; otherwise fall back to the bare SOT triple.
        if prev_text_tokens:
            prompt = (
                [prev_token_id]
                + prev_text_tokens[-_MAX_PROMPT_TOKENS:]
                + sot_tokens
            )
        else:
            prompt = sot_tokens

        res = generate(
            features,
            [prompt],
            beam_size=_BEAM_SIZE,
            sampling_temperature=0.0,
            repetition_penalty=_REPETITION_PENALTY,
        )[0]

        token_ids = res.sequences_ids[0]
        text_tokens = [t for t in token_ids if t < timestamp_begin]
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]

        text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

        # Context-reset policy: only carry a clean (non-degenerate) decode
        # forward. A degenerate window resets the prompt to avoid seeding a
        # propagating hallucination in the next window.
        if text and _compression_ratio(text) <= _COMPRESSION_RATIO_THRESHOLD:
            prev_text_tokens = text_tokens
        else:
            prev_text_tokens = []

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
