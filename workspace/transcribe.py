"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every confidence signal in the ledger so far is read at
WINDOW granularity (``res.scores[0]``, the length-normalised sequence
avg-logprob; ``no_speech_prob``, iter006) or at HYPOTHESIS granularity (the
beam/sample N-best the MBR and lexicon-rerank lineages voted over). The
ctranslate2 Whisper object that ``load()`` returns exposes a third, finer
channel that no iteration has ever called: ``model.align()``. Given the encoder
output and the decoded text tokens, it returns ``text_token_probs`` — the
model's acoustic support for *each individual token*, independent of the
language-model context around it.

This is exactly the signal the window-level score cannot give. A flat-posterior
phone-band obstruent substitution (the dominant 57% axis) is emitted from the
decoder's language prior with little acoustic evidence, so its per-token prob is
low even when the window's overall avg-logprob looks perfectly healthy
(length_ratio 0.96). The window-level gate, the N-best consensus, and the
lexicon rerank are all structurally blind to it — they only ever see the whole
sequence, never which token inside it the audio failed to support.

The mechanism here replaces the incumbent's window-level temperature-fallback
loop with a single clean beam decode followed by a per-token confidence pass:

  - decode the window once with beam search at T=0;
  - re-encode once (``model.encode``) and call ``model.align()`` on the decoded
    text tokens to read ``text_token_probs`` (per-token acoustic support);
  - excise tokens whose acoustic support falls below a floor — these are the
    acoustically-unsupported tokens the decoder hallucinated/substituted from
    its prior rather than from the audio.

This attacks the substitution axis at TOKEN granularity and is cheaper than the
incumbent's per-window fallback loop (one beam pass + one cheap align/encode vs
up to five decodes).

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

# Mel feature frame rate (16 kHz / 160-sample hop = 100 frames/s); align() takes
# num_frames in these units to bound the cross-attention DTW (faster-whisper's
# convention when calling Whisper.align on the encoder output).
_FEATURE_FRAMES_PER_SECOND = 100
_MAX_FEATURE_FRAMES = 3000

# Per-token acoustic-support floor. text_token_probs is a probability in (0,1];
# a token below this was emitted with negligible acoustic evidence — the
# decoder's language prior, not the audio. At 0.15 the floor also cut genuine
# but acoustically-weak Korean grammatical morphemes (short particles 은/는/이/가,
# sentence-final endings) that carry little energy on the 300-3400 Hz band yet
# are correct — manufacturing the parent's deletion rise (0.27->0.35) without
# moving the substitution axis (excising a sub yields a del, no net gain).
# A near-zero floor excises only genuinely unsupported tokens (true
# hallucinations) and spares the weak-but-real morphemes.
_TOKEN_PROB_FLOOR = 0.05


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

        res = generate(
            features,
            [sot_tokens],
            beam_size=_BEAM_SIZE,
            sampling_temperature=0.0,
        )[0]

        token_ids = res.sequences_ids[0]
        text_tokens = [t for t in token_ids if t < timestamp_begin]
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]

        # Per-token acoustic-support pass. align() needs the encoder output and
        # the decoded text tokens; text_token_probs comes back aligned 1:1 with
        # the text tokens passed in, so a token-wise floor is a direct excision.
        if text_tokens:
            encoder_output = model.encode(features)
            num_frames = min(
                _MAX_FEATURE_FRAMES,
                int(chunk_seconds * _FEATURE_FRAMES_PER_SECOND),
            )
            align_res = model.align(
                encoder_output,
                sot_tokens,
                [text_tokens],
                num_frames,
            )[0]
            token_probs = align_res.text_token_probs
            kept = [
                tok
                for tok, prob in zip(text_tokens, token_probs)
                if prob >= _TOKEN_PROB_FLOOR
            ]
        else:
            kept = text_tokens

        text = tokenizer.decode(kept, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
