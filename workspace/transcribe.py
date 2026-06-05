"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. The dominant error axis has flipped from coverage/substitution
to **over-generation**: insertion 11%, hallucination 36%, repeated-text 36%.
The decoder emits text that is not in the audio. The per-file diagnosis shows
*where*: every ``hallucination_hit`` / ``repeated_text`` flag lands on a file
with a high silence fraction (silence_ratio 0.19, 0.25, 0.13) and long silent
gaps (longest_silence 22–34 s). This is Whisper's documented silence-
hallucination failure — fed a 30 s window that is mostly silence, beam search
still produces a fluent, confident, *invented* Korean sentence.

No prompt/glossary/beam change can fix this, because the wrong text is emitted
with high token likelihood; the signal that the window is empty lives in a
**different return channel** that every prior iteration discarded.

`frozen.asr_backend.generate` forwards ``**decoding_kwargs`` straight to
``ctranslate2.models.Whisper.generate``, whose docstring lists
``return_no_speech_prob``. With that flag set, each result object carries a
``no_speech_prob`` field — the decoder's own probability that the window is
non-speech (mass on the ``<|nospeech|>`` token). This is a *gate*, not a
coverage lever: when a window scores high no-speech, we drop its decoded text
rather than splice an invented sentence into the transcript.

So the mechanism this iteration introduces is a **no-speech gate** on the
decode return channel:

  - decode each window with ``return_no_speech_prob=True``;
  - if ``no_speech_prob`` exceeds a threshold, emit nothing for that window
    and advance by a fixed hop (the window held no speech to transcribe);
  - otherwise emit the text and advance content-adaptively via the timestamp
    channel as before.

We also drop the static glossary prompt: it biased the language prior (a
substitution lever) but does nothing for over-generation, and a non-empty
``<|startofprev|>`` prefix actively *encourages* the decoder to keep
generating on silence. The bare SOT prompt gives the no-speech token its
cleanest shot at winning the gate.

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

# No-speech gate. CT2 reports no_speech_prob in [0, 1]; on the diagnosis
# files the hallucinated windows are the silence-dominated ones. A high
# threshold keeps real speech (which scores low) while dropping windows the
# decoder itself flags as non-speech, where its emitted text is invented.
_NO_SPEECH_THRESHOLD = 0.6

# When a window is gated as non-speech we cannot trust its timestamp tokens to
# tell us how far to advance, so we hop a fixed amount through the silence.
_SILENCE_HOP_SECONDS = 25.0


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
            beam_size=5,
            sampling_temperature=0.0,
            return_no_speech_prob=True,
        )[0]

        # The no-speech gate: trust the decoder's own emptiness estimate over
        # its emitted tokens. On a non-speech window the text is hallucinated.
        no_speech_prob = getattr(res, "no_speech_prob", 0.0)
        if no_speech_prob >= _NO_SPEECH_THRESHOLD:
            cursor += max(1, int(_SILENCE_HOP_SECONDS * sr))
            continue

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
