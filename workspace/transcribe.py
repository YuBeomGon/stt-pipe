"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prior iteration on this job conditioned on the *decode*
side (beam_size, temperature, num_hypotheses, scores, no_speech_prob,
repetition_penalty, patience) or on *windowing* (timestamp advance, energy VAD,
overlap stitch). Not one ever transformed the **waveform** before it reached
``processor(...)``. iter013 computed per-frame RMS but used it only to choose
cut points; the samples themselves always went through untouched.

That is the unexplored input surface this slot attacks. The frozen backend's
``WhisperProcessor`` runs Whisper's standard feature extractor, whose mel
normalisation is a **fixed affine** (``(log_spec + 4.0) / 4.0``) — it clamps
dynamic range but does NOT compensate for absolute input gain. So a file at
``rms_db_mean ≈ -41`` (00003011051, 02_4038) lands the acoustic encoder in a
systematically different log-mel magnitude regime than a file at ``-26``
(00003092151), even though the spoken content is comparable. The diagnosis
shows a ~15 dB spread in ``rms_db_mean`` across the batch — a free, per-file
offset the encoder has to absorb, and phone-band audio (where substitution
concentrates) is exactly where that extra burden tips a marginal phoneme into
the wrong token.

The mechanism: **loudness-normalise the whole waveform to a fixed target RMS
before any windowing or decode**, with a peak guard so the gain can never clip.
This is applied once over the entire utterance (not per window) on purpose —
the within-file ``rms_db_p05/p95`` spread is the speech/silence dynamic range,
which must be preserved; only the *cross-file* gain offset is removed. The
result is that every file presents the encoder with a consistent absolute
level, so the fixed mel normalisation sees a consistent operating point and the
encoder spends none of its capacity on gain.

The decode side is deliberately stripped back to a single beam-search pass per
window (no temperature-fallback loop, no compression gate, no MBR vote) so the
effect of the waveform conditioning is measured in isolation rather than
confounded with the incumbent's decode machinery.

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

# Target waveform RMS (~-20 dBFS). Whisper's feature extractor normalises the
# log-mel with a fixed affine and never compensates for input gain, so bringing
# every file to a common absolute level puts the acoustic encoder at a
# consistent operating point across the batch's ~15 dB cross-file gain spread.
_TARGET_RMS = 0.1

# Peak ceiling for the normalisation gain. The gain is capped so the loudest
# sample never exceeds this, i.e. loudness normalisation can never introduce
# clipping (which would itself create phone-band-style distortion).
_PEAK_CEILING = 0.99


def _normalize_loudness(audio: np.ndarray) -> np.ndarray:
    """Scale the whole utterance to a fixed target RMS, guarded against clip.

    A single global gain preserves the file's internal speech/silence dynamic
    range (the within-file RMS spread) while removing the cross-file gain offset
    the fixed mel normalisation cannot absorb.
    """
    rms = float(np.sqrt(np.mean(np.square(audio))))
    if rms < 1e-6:
        return audio  # essentially silent — nothing to normalise toward
    gain = _TARGET_RMS / rms
    peak = float(np.max(np.abs(audio)))
    if peak > 0.0 and peak * gain > _PEAK_CEILING:
        gain = _PEAK_CEILING / peak
    return audio * gain


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    audio = _normalize_loudness(audio)

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
