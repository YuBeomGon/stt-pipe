"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prior iteration ran a *single-shot* decode per window and
read at most two return channels: ``sequences_ids`` (the text/timestamp tokens)
and ``no_speech_prob`` (iter006). The decode result carries a third channel
that no iteration has ever read — the per-sequence **score**, available when
``generate(..., return_scores=True)`` is set. CT2 returns it length-normalised,
so ``res.scores[0]`` is a usable proxy for the decoder's average log-prob over
the window: a direct confidence signal for *how reliable this window's
transcription is*, distinct from no_speech_prob (which asks "is this speech?"
not "is this decode trustworthy?").

That score channel is exactly what OpenAI's reference Whisper uses for its
**temperature-fallback** long-form policy, which no iteration here has built.
The mechanism this slot introduces replaces the single-shot decode with an
adaptive per-window loop:

  - decode at temperature 0.0 with beam search (the high-precision attempt);
  - score the result on two signals the score channel makes available — the
    length-normalised avg log-prob (``res.scores[0]``) and the gzip
    *compression ratio* of the emitted text (a degenerate, repeated, or
    hallucinated window compresses far more than natural speech);
  - if either signal flags the decode as untrustworthy (avg log-prob too low,
    or compression ratio too high), re-decode the *same* window at a higher
    sampling temperature and try again, walking a temperature schedule;
  - keep the best-scoring attempt seen if none clears the gate.

This is a fundamentally different decode strategy than the incumbent's fixed
beam+no_speech gate: instead of a single irreversible decode, each window gets
multiple attempts and the score channel arbitrates. It attacks the residual
hallucination (0.18) / repeated-text (0.27) directly via the compression gate,
and the low-confidence windows — where phone-band substitution concentrates —
get a second, higher-entropy shot to escape a confidently-wrong beam path.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import zlib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

_WINDOW_SECONDS = 30.0
_TIME_PRECISION = 0.02
_MIN_ADVANCE_SECONDS = 2.0

# Temperature-fallback schedule (OpenAI Whisper's robustness policy). The first
# attempt is beam search at T=0 (precision); each fallback raises entropy so the
# decoder can escape a degenerate or confidently-wrong path.
_TEMPERATURE_SCHEDULE = (0.0, 0.2, 0.4, 0.6, 0.8)

# Gate thresholds read off the score channel. avg log-prob below the floor =>
# the decoder is unsure of this window; gzip compression ratio above the ceiling
# => the text is repetitive/hallucinated (natural speech ~1.3-1.8, degenerate
# loops >> 2.4). Either condition triggers a higher-temperature retry.
_LOGPROB_THRESHOLD = -1.0
_COMPRESSION_RATIO_THRESHOLD = 2.4

_BEAM_SIZE = 5


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

        # Temperature-fallback loop. Decode the window, score it off the score
        # channel, and re-decode hotter until a trustworthy attempt is found or
        # the schedule is exhausted (then keep the best-scoring attempt).
        best = None  # (avg_logprob, res)
        chosen = None
        for temp in _TEMPERATURE_SCHEDULE:
            if temp == 0.0:
                res = generate(
                    features,
                    [sot_tokens],
                    beam_size=_BEAM_SIZE,
                    sampling_temperature=0.0,
                    return_scores=True,
                )[0]
            else:
                res = generate(
                    features,
                    [sot_tokens],
                    beam_size=1,
                    sampling_temperature=temp,
                    return_scores=True,
                )[0]

            avg_logprob = res.scores[0] if res.scores else float("-inf")

            token_ids = res.sequences_ids[0]
            text_tokens = [t for t in token_ids if t < timestamp_begin]
            text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
            comp_ratio = _compression_ratio(text)

            if best is None or avg_logprob > best[0]:
                best = (avg_logprob, res)

            if avg_logprob >= _LOGPROB_THRESHOLD and comp_ratio <= _COMPRESSION_RATIO_THRESHOLD:
                chosen = res
                break

        if chosen is None:
            chosen = best[1]

        token_ids = chosen.sequences_ids[0]
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
