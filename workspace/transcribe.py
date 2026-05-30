"""Workspace transcribe — autoresearch evolves this file.

Long-form coverage via timestamp-guided sequential windows. Each window is
decoded WITH Whisper timestamp tokens enabled (the SOT prompt omits
``<|notimestamps|>``), so the decoder emits ``<|t.tt|>`` tokens that survive in
``sequences_ids`` (any id at or above the ``<|0.00|>`` token id; each step above
it is 0.02s). The last such timestamp tells us where the decoder believes the
final complete utterance ended inside the window — so the next window seeks to
that point instead of a blind fixed 30s stride. This is the canonical OpenAI
long-form seek: it stops slicing an utterance that straddles a 30s boundary,
which is the boundary-cut that drives the deletion + repeated-text collapse on
this batch. Windows are still conditioned on the previous window's clean text
through the ``<|startofprev|>`` prefix for cross-window coherence.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30
_MAX_PREV_TOKENS = 224  # Whisper prompt context budget
_TS_STEP_SECONDS = 0.02  # Whisper timestamp token resolution
_MIN_ADVANCE_FRACTION = 0.5  # floor seek advance to bound runtime / prevent stall
_DOMAIN_BUDGET = 24  # tight cap on the domain prior — gentle bias, not forcing

# Static domain-vocabulary prior for the 0715 Korean insurance call-center batch,
# fed through <|startofprev|> on every window. iter_007 showed a full ~40-term
# glossary at budget 64 improved substitution (0.54->0.52) and erased the
# hallucination (0.09->0.00) but pushed insertion up (0.12->0.16): too strong a
# prior forces unspoken domain terms. SYNTHESIS: keep the sub/hal gain, guard the
# insertion regression by trimming to the highest-frequency in-domain terms and a
# much smaller token budget so the decoder is nudged, not over-ridden.
_DOMAIN_PROMPT = (
    "고객님 보험 계약 약관 보장 가입 청약 해지 보험료 납입 "
    "보험금 본인확인 상담사 동의 청구 확인"
)

_EQ_BANDS = 32  # coarse magnitude bands estimating the channel envelope
_EQ_CLIP = 3.0  # max boost/cut of any band — gentle channel correction, not whitening


def _equalize_channel(audio: np.ndarray) -> np.ndarray:
    """Blind channel (spectral-coloration) equalization on the full waveform.

    NEW MECHANISM (EXPLORE): every prior waveform lever reshaped the signal
    only by a fixed spectral tilt (pre-emphasis, iter_024/025), a flat level
    rescale (RMS, iter_026), an out-of-band mask (band-limit, iter_029/043/051),
    or a memoryless amplitude nonlinearity (companding, iter_054). None touched
    the *in-band spectral ENVELOPE*. Telephony (handset + codec) imposes a fixed
    band coloration that tilts that envelope and shifts formant energy ratios —
    the kind of channel distortion that drives consonant/vowel substitution, the
    dominant error axis. We estimate the channel as the coarse (32-band) magnitude
    envelope and divide it out, flattening the coloration toward the training
    distribution while the coarse binning leaves fine formant structure intact and
    the ±3x clip keeps it a gentle EQ (not full whitening that would amplify the
    inter-formant noise floor). Phase is preserved; level is restored to the input
    RMS so downstream mel scaling is unchanged. numpy-only, once per file (outside
    the window loop) — budget-neutral.
    """
    n = len(audio)
    if n < _EQ_BANDS * 2:
        return audio
    spec = np.fft.rfft(audio)
    mag = np.abs(spec)
    m = mag.size
    binsz = int(np.ceil(m / _EQ_BANDS))
    padded = np.pad(mag, (0, binsz * _EQ_BANDS - m), mode="edge")
    coarse = padded.reshape(_EQ_BANDS, binsz).mean(axis=1)
    channel = np.repeat(coarse, binsz)[:m]
    channel = channel / (np.median(channel) + 1e-8)
    channel = np.clip(channel, 1.0 / _EQ_CLIP, _EQ_CLIP)
    out = np.fft.irfft(spec / channel, n=n).astype(np.float32)
    in_rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) + 1e-8
    out_rms = float(np.sqrt(np.mean(out.astype(np.float64) ** 2))) + 1e-8
    return (out * (in_rms / out_rms)).astype(np.float32)


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tok = processor.tokenizer

    # flatten the fixed telephony channel coloration once before any windowing,
    # so the encoder sees a spectral envelope closer to the training distribution.
    audio = _equalize_channel(audio)

    # NOTE: <|notimestamps|> is intentionally omitted so the decoder emits
    # timestamp tokens; they are stripped from the text by skip_special_tokens
    # but read out of the raw sequence to drive the seek.
    sot = tok.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    startofprev = tok.convert_tokens_to_ids("<|startofprev|>")
    timestamp_begin = tok.convert_tokens_to_ids("<|0.00|>")

    # encode the tight domain prior once; cap it so it only nudges the lexical
    # prior toward in-domain terms without crowding out the cross-window context.
    domain_ids = tok.encode(_DOMAIN_PROMPT, add_special_tokens=False)[:_DOMAIN_BUDGET]

    chunk_len = _CHUNK_SECONDS * sr
    min_advance = int(_CHUNK_SECONDS * _MIN_ADVANCE_FRACTION * sr)
    n_samples = len(audio)

    texts: list[str] = []
    prev_ids: list[int] = []
    seek = 0
    while seek < n_samples:
        seg = audio[seek : seek + chunk_len]
        if seg.size == 0:
            break

        inputs = processor(seg, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        # domain prior leads the context on every window (incl. window 0, which
        # previously had no prefix); the remaining budget carries the previous
        # window's clean text for cross-window coherence.
        prev_budget = max(_MAX_PREV_TOKENS - len(domain_ids), 0)
        context = domain_ids + prev_ids[-prev_budget:] if prev_budget else domain_ids
        prompt = [startofprev] + context + sot

        results = generate(
            features,
            [prompt],
            beam_size=4,
            patience=1.2,
            sampling_temperature=0.0,
            repetition_penalty=1.1,
            no_repeat_ngram_size=4,
        )

        token_ids = results[0].sequences_ids[0]
        text = tok.decode(token_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text)
            # re-encode the clean decoded text as next window's context, so EOT
            # / timestamp ids never leak into the <|startofprev|> prefix
            prev_ids = tok.encode(text.strip(), add_special_tokens=False)

        # find the last timestamp token to decide where the next window starts
        seg_seconds = seg.shape[0] / sr
        last_ts_seconds = None
        for tid in reversed(token_ids):
            if tid >= timestamp_begin:
                last_ts_seconds = (tid - timestamp_begin) * _TS_STEP_SECONDS
                break

        if last_ts_seconds is not None:
            advance = int(min(last_ts_seconds, seg_seconds) * sr)
        else:
            advance = chunk_len
        # a degenerate early timestamp would re-decode most of the window and
        # blow the runtime budget (or stall) — floor the stride.
        advance = max(advance, min_advance)
        seek += advance

    return " ".join(t.strip() for t in texts if t.strip())
