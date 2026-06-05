"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prior iteration treated the decoder's *output vocabulary*
as fixed: every decode ran with the CT2 default ``suppress_tokens=[-1]`` (only
the model's built-in symbol set masked). No iteration ever constrained *which*
content tokens the decoder is allowed to emit. That is an entirely unused
input channel of ``generate()`` — orthogonal to every decode knob in the
ledger (beam_size, temperature, patience, repetition/length penalty, the
score/no_speech/N-best return channels) and to the input-side gain/spectral
conditioning of iter_018-032.

Motivation from the dominant axis. The error mix is 57% substitution on a
healthy length_ratio (0.96): the headroom is in *what* gets mis-recognised.
whisper-large-v3-turbo is a multilingual model, and on telephony-band Korean
(300-3400 Hz, obstruent cues attenuated) it routinely resolves an ambiguous
Korean syllable to a phonetically-adjacent token in a *different script* —
Hanja (CJK ideographs) or Japanese kana — which the Korean ground truth never
contains. Every such emission is a guaranteed substitution error.

The mechanism: scan the tokenizer vocabulary once and build a suppression set
of every token whose surface form contains a CJK-ideograph / Hiragana /
Katakana / CJK-compatibility codepoint, then pass it (alongside the ``-1``
default sentinel) as ``suppress_tokens`` to every decode. The decoder keeps
full Hangul, ASCII (numbers, the occasional English loanword), and punctuation
mass, but is forbidden from spending probability on scripts that can only be
wrong here. This removes a substitution failure mode at the source rather than
triaging it post-hoc.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

_WINDOW_SECONDS = 30.0
_TIME_PRECISION = 0.02
_MIN_ADVANCE_SECONDS = 2.0

_BEAM_SIZE = 5

# Per-token repetition penalty (iter_016, best 0.1629). The parent's CJK-mask
# decode left this at the CT2 default 1.0; ~1.1 steers the beam off a
# self-repeating / locally-tempting wrong token during decode itself —
# orthogonal to the suppress_tokens script mask.
_REPETITION_PENALTY = 1.1

# Codepoint ranges that can only be a substitution error in a Korean transcript:
# Hiragana/Katakana, CJK unified ideographs (+ ext-A and compatibility forms).
# Hangul (U+AC00-D7A3, U+1100-11FF, U+3130-318F) is deliberately NOT here.
_FORBIDDEN_RANGES = (
    (0x3040, 0x30FF),    # Hiragana + Katakana
    (0x3400, 0x4DBF),    # CJK unified ideographs ext A
    (0x4E00, 0x9FFF),    # CJK unified ideographs
    (0xF900, 0xFAFF),    # CJK compatibility ideographs
    (0x20000, 0x2FA1F),  # CJK ext B..F + supplement
)

# Built once on first call (vocab scan is ~50k cheap decodes) and reused.
_SUPPRESS_TOKENS: list[int] | None = None


def _is_forbidden_char(ch: str) -> bool:
    o = ord(ch)
    for lo, hi in _FORBIDDEN_RANGES:
        if lo <= o <= hi:
            return True
    return False


def _build_suppress_tokens(tokenizer) -> list[int]:
    # -1 keeps CT2's default built-in symbol suppression; we extend it with
    # every vocab token whose decoded form carries a forbidden-script char.
    suppress = [-1]
    for tid in range(tokenizer.vocab_size):
        surface = tokenizer.decode([tid])
        if any(_is_forbidden_char(ch) for ch in surface):
            suppress.append(tid)
    return suppress


def transcribe(audio: np.ndarray, sr: int) -> str:
    global _SUPPRESS_TOKENS
    model, processor = load()
    tokenizer = processor.tokenizer

    if _SUPPRESS_TOKENS is None:
        _SUPPRESS_TOKENS = _build_suppress_tokens(tokenizer)

    audio = np.asarray(audio, dtype=np.float32)
    n_samples = audio.shape[0]
    win_samples = int(_WINDOW_SECONDS * sr)

    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")
    sot_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )

    pieces: list[str] = []
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

        res = generate(
            features,
            [sot_tokens],
            beam_size=_BEAM_SIZE,
            sampling_temperature=0.0,
            repetition_penalty=_REPETITION_PENALTY,
            suppress_tokens=_SUPPRESS_TOKENS,
        )[0]

        token_ids = res.sequences_ids[0]
        text_tokens = [t for t in token_ids if t < timestamp_begin]
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]

        text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
