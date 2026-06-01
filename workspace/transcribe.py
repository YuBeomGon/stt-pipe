"""Workspace transcribe — autoresearch evolves this file.

The standing best (iter_018: adaptive timestamp-seek + beam=3 / patience=4.0 /
length_penalty=1.1) has coverage saturated (length_ratio 0.96) but stalled at
cer 0.1932 for 10 iters under pure scoring-knob tuning. The per-file diagnosis
shows where the remaining mass lives: it is NOT a coverage problem. The worst
file (01_8088…10_01_57, cer 0.407) has length_ratio 0.99 yet carries
``repeated_text`` — full length, wrong text — and ~half the batch shares that
flag (repeated 0.45). A scoring knob cannot fix this: once the beam has fallen
into a degenerate repeat run, re-ranking the same poisoned candidate set just
re-picks a poisoned hypothesis, and the collapsed run reads as substitution.

This iteration synthesises the iter_009 mechanism (zlib-compression-gated
**temperature fallback**, which improved the substitution axis but regressed
coverage off a weaker parent) onto the current best beam pipeline. The two
unused surface capabilities it rests on (ledger iter_009): ``sampling_topk=0``
makes ``sampling_temperature`` actually sample the full distribution (with the
default topk=1 the decoder is argmax and temperature is inert), and a window's
zlib compression ratio spikes exactly on a repeat loop. So: decode each window
with the current best beam config first; only if its text compresses
suspiciously well (ratio > 2.4 ⇒ repetition) re-decode at escalating
temperature with full sampling to break the loop, keeping whichever pass
compresses least.

Coverage guard for iter_009's regression: a CLEAN window (ratio <= threshold)
takes the temp=0.0 beam result unchanged and is byte-identical to the standing
best, so it cannot regress; the fallback touches only the few repetition-prone
windows. The adaptive seek follows the kept (cleaner) result, and the
min_advance floor still bounds the decode-call count, so runtime stays inside
the ~190s/719.9s the parent measured even with the handful of extra re-decodes.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import zlib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
# Floor on how far each window advances. Without it a window that stamps a very
# early stop would advance only a few seconds and multiply the decode-call
# count (runtime). 20s caps the worst-case window growth at 1.5x while leaving
# the typical ~22-29s last-segment advance untouched.
_MIN_ADVANCE_SECONDS = 20

# Temperature-fallback schedule. The first attempt (0.0) is exactly the parent's
# beam decode, so clean windows incur zero extra cost and byte-match the best.
# Higher temps are tried only when a window is judged a repetition loop, so the
# runtime hit is bounded to the (few) repeated_text-prone windows.
_FALLBACK_TEMPS = (0.0, 0.4, 0.8)
# Whisper's standard repetition gate: a degenerate repeat run compresses far
# better than natural speech, so its zlib ratio (raw/compressed bytes) spikes
# above ~2.4. Below the threshold the window is accepted as-is.
_COMPRESSION_RATIO_THRESHOLD = 2.4


def _compression_ratio(text: str) -> float:
    """zlib compression ratio (raw bytes / compressed bytes) of the decoded
    text. A repeated run compresses far better than natural speech, so this
    spikes exactly on the repetition loop that the temperature fallback targets.
    """
    if not text:
        return 0.0
    data = text.encode("utf-8")
    return len(data) / len(zlib.compress(data))


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio).reshape(-1)
    window = int(_WINDOW_SECONDS * sr)
    min_advance = int(_MIN_ADVANCE_SECONDS * sr)
    n = len(audio)

    # Timestamp-decoding prompt: NO <|notimestamps|>, so the decoder emits
    # <|t|> boundaries; their offsets drive the adaptive stride below.
    prompt_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    # Every token id >= <|0.00|> is a timestamp token. offset_s = (id - begin)*0.02.
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    texts = []
    seek = 0
    while seek < n:
        chunk = audio[seek : seek + window]
        if len(chunk) == 0:
            break
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        # input_features is (1, n_mels, n_frames); the extractor pads the final
        # short chunk up to the full 30s window for us.
        features = to_storage_view(inputs.input_features)

        # Temperature fallback: decode with the parent's beam config first; only
        # re-decode (sampling the full distribution to escape a repeat loop) if
        # the text compresses suspiciously well. Keep the least-compressible
        # (least-repetitive) pass. Clean windows stop after temp=0.0 and are
        # byte-identical to the standing best, so they cannot regress.
        best_ids = None
        best_ratio = None
        for temp in _FALLBACK_TEMPS:
            if temp == 0.0:
                # Beam=3 / patience=4.0 / length_penalty=1.1 — the iter_018 best.
                results = generate(
                    features,
                    [prompt_tokens],
                    beam_size=3,
                    patience=4.0,
                    length_penalty=1.1,
                    sampling_temperature=0.0,
                )
            else:
                # sampling_topk=0 makes sampling_temperature actually sample the
                # full distribution (default topk=1 is argmax, temperature inert),
                # giving the decoder a path OUT of the repeat run the beam locked.
                results = generate(
                    features,
                    [prompt_tokens],
                    beam_size=1,
                    sampling_topk=0,
                    sampling_temperature=temp,
                )

            token_ids = results[0].sequences_ids[0]
            text_ids = [t for t in token_ids if t < timestamp_begin]
            text = tokenizer.decode(text_ids, skip_special_tokens=True)
            ratio = _compression_ratio(text)
            if best_ratio is None or ratio < best_ratio:
                best_ratio = ratio
                best_ids = token_ids
            # Accept the first non-degenerate pass; no re-decode for clean windows.
            if ratio <= _COMPRESSION_RATIO_THRESHOLD:
                break

        token_ids = best_ids
        text_ids = [t for t in token_ids if t < timestamp_begin]
        texts.append(tokenizer.decode(text_ids, skip_special_tokens=True))

        # Final partial window: extractor padded it, nothing follows — stop.
        if len(chunk) < window:
            break

        # Advance to the end of the last emitted segment (last timestamp token)
        # of the KEPT result. If the window emitted no timestamp, full stride.
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]
        if ts_tokens:
            advance = int((ts_tokens[-1] - timestamp_begin) * 0.02 * sr)
            advance = max(advance, min_advance)
        else:
            advance = window
        seek += advance

    return " ".join(t.strip() for t in texts if t.strip())
