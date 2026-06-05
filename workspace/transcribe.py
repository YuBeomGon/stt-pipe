"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prior input-side iteration (iter_018-021) conditioned only
the *gain* of the waveform: a scalar multiply to hit an RMS/peak target. None
ever touched its *spectrum*. This iteration introduces a fundamentally
different input-conditioning axis — **pre-emphasis spectral shaping** — and
removes the entire temperature-fallback / MBR decode machinery the incumbent
carried, decoding each window with a single clean beam pass instead.

Motivation from the dominant axis. The error mix is 57% substitution with a
healthy length_ratio (0.96), so the headroom is in *what* gets mis-recognised,
not coverage. This is call-center audio: a telephony channel band-limits the
signal to roughly 300-3400 Hz, which attenuates precisely the high-frequency
energy (fricative/affricate noise bursts, stop releases) that distinguishes
Korean obstruents — ㅅ/ㅆ, ㅈ/ㅊ, ㄱ/ㅋ, plosive vs aspirated. A decoder starved
of those cues falls back on its language prior and substitutes a
phonetically-adjacent word: the loop-substitution failure on phone-band audio.

A first-order pre-emphasis filter ``y[n] = x[n] - alpha * x[n-1]`` is the
classical fix — a +6 dB/octave high-shelf that restores the spectral tilt the
phone channel removed, applied to the raw np.ndarray *before* the frozen
WhisperProcessor's fixed mel extraction (which only normalises log-magnitude
and never compensates spectral tilt). This is orthogonal to every decode knob
in the ledger and to the gain transforms of iter_018-021: it changes the
acoustic evidence the encoder receives, not how the decoder searches over it.

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

# Pre-emphasis coefficient. The standard speech-processing value (~0.97) applies
# a first-order high-pass y[n] = x[n] - alpha*x[n-1], boosting energy above
# ~1 kHz by roughly +6 dB/octave to undo the spectral tilt of the telephony
# band-pass. This restores the high-frequency consonant cues a phone channel
# attenuates — the cues whose loss drives phone-band substitution — without
# touching the decode search at all.
_PREEMPHASIS_ALPHA = 0.97


def _preemphasis(audio: np.ndarray, alpha: float) -> np.ndarray:
    """First-order pre-emphasis high-pass; preserves length, RMS-rescaled.

    Pre-emphasis lowers broadband RMS (it removes low-frequency energy), so we
    restore the original RMS afterwards to keep the waveform inside the same
    dynamic range the fixed mel normalisation expects — the spectral *shape*
    is what changes, not the overall level.
    """
    if audio.shape[0] < 2:
        return audio
    filtered = np.empty_like(audio)
    filtered[0] = audio[0]
    filtered[1:] = audio[1:] - alpha * audio[:-1]

    orig_rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    new_rms = float(np.sqrt(np.mean(filtered.astype(np.float64) ** 2)))
    if new_rms > 1e-8:
        filtered = filtered * (orig_rms / new_rms)
    return filtered.astype(np.float32)


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    audio = _preemphasis(audio, _PREEMPHASIS_ALPHA)

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
