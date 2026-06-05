"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. The dominant failure is substitution (57%): phone-band domain
terms decoded as a phonetic neighbour, on otherwise full-coverage audio. The
context-conditioning lineage (iters 071-075) attacked this by feeding the
*previous window's* decoded text back as a ``<|startofprev|>`` decoder prior —
but iter_072 established the structural flaw: a single-window carry-forward is
recency-only, so a substitution-prone window is promoted as the next window's
authoritative prior verbatim, propagating its own error forward (the insertion
rise 0.08->0.12 that cancelled its substitution drop).

This slot introduces a fundamentally different prior: a **global,
frequency-gated term bank** accumulated across the whole call. Instead of
carrying the last window's transient text, we maintain a running ``Counter`` of
the substantive (multi-char) text tokens decoded so far, and prime each window
with the tokens seen **at least twice** — the call's stable, repeated
vocabulary. The mechanism is single-pass and self-consistent: a domain term
the model recognises confidently in a clean, high-energy window enters the bank
and then biases the decoder's prior on a later band-degraded window where the
same term would otherwise lose to its phonetic neighbour. Crucially the ≥2
frequency gate is an error filter the sequential carry-forward never had: a
one-off mis-recognition occurs once, never reaches count 2, and so can never
poison the prior — directly answering iter_072's propagation failure mode.

Two further guards keep the bank clean. A window whose text fails a gzip
compression-ratio check (degenerate/looping) is emitted for coverage but its
tokens are *not* contributed to the bank, so a hallucinated window cannot seed
future priors. ``repetition_penalty`` is lifted off neutral to 1.1: iter_074
established it acts over the carried ``<|startofprev|>`` span, and the parent
left it at 1.0 on the premise that a banked term *should* re-emit freely. But
the lineage's regression (hal 0.27->0.45 across iters 099-101) shows the bank
drives far more *spurious* re-emission than helpful re-emission — the decoder
drifts into emitting banked tokens the current window does not acoustically
support. A mild 1.1 penalty taxes that drift while leaving genuine acoustic
evidence strong enough to re-emit a truly-spoken term, rebalancing the prime
toward precision against the dominant substitution axis without re-injecting
the insertions/hallucinations the neutral setting let through.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import zlib
from collections import Counter

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

_WINDOW_SECONDS = 30.0
_TIME_PRECISION = 0.02
_MIN_ADVANCE_SECONDS = 2.0

_BEAM_SIZE = 5

# Penalty applied by CT2 against any token id already present in the sequence —
# and the bank-primed <|startofprev|> span IS part of that sequence (iter_074).
# Lifted slightly above neutral so the decoder is taxed for drifting into
# re-emitting banked tokens the current window does not support (the lineage's
# hal regression) while real acoustic evidence still overrides the penalty.
_REPETITION_PENALTY = 1.1

# Global term-bank parameters. A text token must be decoded at least
# _BANK_MIN_COUNT times across prior windows before it is eligible to prime
# later windows — the frequency gate that distinguishes the call's stable
# domain vocabulary from a one-off substitution. The bank is capped at
# _BANK_MAX_TOKENS most-frequent tokens to keep the <|startofprev|> span short
# (iter_073: a long carried span is a re-emission/insertion liability) and well
# under Whisper's prompt-context budget.
_BANK_MIN_COUNT = 2
_BANK_MAX_TOKENS = 48

# Windows whose text compresses above this ratio (gzip) are degenerate/looping
# (natural Korean speech ~1.3-1.8; repeated loops >> 2.4). Such a window is
# still emitted for coverage but is BARRED from contributing to the bank, so a
# hallucinated window cannot seed the prior for every window after it.
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
    startofprev = tokenizer.convert_tokens_to_ids("<|startofprev|>")
    sot_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )

    # Running global vocabulary of the call. Counter over substantive text
    # token ids; _token_str caches per-token decoded strings so the
    # substantive() filter never re-decodes a token id.
    bank_counts: Counter[int] = Counter()
    _token_str: dict[int, str] = {}

    def _substantive(tid: int) -> bool:
        s = _token_str.get(tid)
        if s is None:
            s = tokenizer.decode([tid]).strip()
            _token_str[tid] = s
        return len(s) >= 2 and any(ch.isalnum() for ch in s)

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

        # Build the decoder prompt: the frequency-gated global bank prepended as
        # a <|startofprev|> context span ahead of the fixed SOT triple. The bank
        # holds only tokens seen >= _BANK_MIN_COUNT times, capped to the most
        # frequent _BANK_MAX_TOKENS — the call's stable register, ordered by
        # frequency so the strongest domain cues sit closest to the SOT.
        bank = [t for t, c in bank_counts.most_common() if c >= _BANK_MIN_COUNT]
        bank = bank[:_BANK_MAX_TOKENS]
        if bank:
            prompt = [startofprev, *bank, *sot_tokens]
        else:
            prompt = list(sot_tokens)

        res = generate(
            features,
            [prompt],
            beam_size=_BEAM_SIZE,
            sampling_temperature=0.0,
            repetition_penalty=_REPETITION_PENALTY,
            return_scores=True,
        )[0]

        token_ids = res.sequences_ids[0]
        text_tokens = [t for t in token_ids if t < timestamp_begin]
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]

        text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)
            # Contribute to the global bank only if the window is non-degenerate,
            # so a looping/hallucinated window is emitted but never poisons the
            # prior for subsequent windows.
            if _compression_ratio(text) <= _COMPRESSION_RATIO_THRESHOLD:
                for t in text_tokens:
                    if _substantive(t):
                        bank_counts[t] += 1

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
