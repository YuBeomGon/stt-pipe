"""Workspace transcribe — autoresearch evolves this file.

Discovery (iter 6, explore): the pipeline has only ever conditioned windowing
on token-side signals (fixed +30 s, then iter_004's last-timestamp seek). It
has never read the *raw-waveform energy envelope* — an input that is sitting in
the ``audio`` argument itself. The dominant axis is coverage/deletion
(del 66%, length_ratio 0.83): a fixed/timestamp cut slices windows blind to
where speech actually pauses, so a window can end mid-utterance and the decoder
restarts cold inside a word, dropping span at the seam.

Mechanism: frame the whole call into 20 ms RMS frames once and derive a global
silence floor (a low percentile of frame energy). For each window, search the
last 10 s of its 30 s span for the quietest frame; if that frame falls below
the silence floor it is a genuine pause, so cut the window there and advance to
exactly that point — windows now begin and end in silence, eliminating the
mid-word seam. When the search region is all above the floor (dense continuous
speech — the worst file, silence_ratio 0.065, 181 s speech runs), no trough
exists, so fall back to iter_005's last-timestamp rewind unchanged. The change
is therefore strictly additive: silence-bearing windows get clean seams, dense
windows behave exactly as before.

iter_005's temperature-fallback decode (scores[0] / no_speech_prob gated
re-roll) is retained verbatim for per-window robustness.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
# CT2 emits a timestamp token every 0.02 s of audio (Whisper's frame stride);
# reuse the same stride to frame the waveform for the RMS envelope.
_TIMESTAMP_RESOLUTION = 0.02
# Only trust a last-timestamp advance (dense-speech fallback) inside this range;
# this also marks the start of the silence-search region (the window's tail).
_MIN_ADVANCE_SECONDS = 20.0
# A frame this far below the call's median energy is treated as a real pause.
# Expressed as a low percentile of the whole-call frame-RMS distribution.
_SILENCE_PERCENTILE = 20.0

# Temperature-fallback schedule (Whisper's native robustness loop). Greedy
# first; climb only when a decode is judged failed. Capped at 4 rungs so the
# worst-case per-window cost stays bounded against the runtime budget.
_TEMPERATURES = (0.0, 0.2, 0.4, 0.6)
# Standard Whisper avg-log-prob gate: at or above this the decode is trusted.
_LOGPROB_THRESHOLD = -1.0
# A window the model is this confident is silence is not worth re-rolling.
_NO_SPEECH_THRESHOLD = 0.6


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio).reshape(-1)
    window = _WINDOW_SECONDS * sr
    n = len(audio)

    # Whole-call RMS envelope (the never-used raw-audio input). Non-overlapping
    # 20 ms frames; the silence floor is a low percentile of frame energy, so it
    # adapts to each call's own noise level rather than a fixed dB.
    hop = max(int(_TIMESTAMP_RESOLUTION * sr), 1)
    n_frames = n // hop
    if n_frames > 0:
        frames = audio[: n_frames * hop].astype(np.float64).reshape(n_frames, hop)
        frame_rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
        silence_floor = float(np.percentile(frame_rms, _SILENCE_PERCENTILE))
    else:
        frame_rms = np.zeros(0)
        silence_floor = 0.0

    # No <|notimestamps|>: keep the decoder in timestamp-emitting mode (the
    # dense-speech fallback still needs the last-timestamp seek).
    prompt_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")

    texts = []
    seek = 0
    while seek < n:
        hard_end = min(seek + window, n)

        # Silence-aware cut: only on a full 30 s window, search its tail
        # (last 10 s) for the quietest frame; cut there iff it is a real pause.
        silence_cut = False
        cut = hard_end
        if hard_end == seek + window and frame_rms.size:
            f0 = (seek + int(_MIN_ADVANCE_SECONDS * sr)) // hop
            f1 = hard_end // hop
            region = frame_rms[f0:f1]
            if region.size:
                local = int(np.argmin(region))
                if region[local] <= silence_floor:
                    cut = (f0 + local) * hop
                    silence_cut = True

        chunk = audio[seek:cut]

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        # Temperature fallback: keep the highest avg-log-prob decode, stopping
        # as soon as one clears the confidence gate (or the window reads as
        # silence). scores[0] and no_speech_prob are the unused return channels.
        best = None
        best_logprob = float("-inf")
        for temp in _TEMPERATURES:
            kwargs = {
                "beam_size": 1,
                "return_scores": True,
                "return_no_speech_prob": True,
            }
            if temp > 0.0:
                # sampling_topk=0 makes CT2 sample the full distribution so the
                # temperature actually perturbs the decode (topk=1 is greedy).
                kwargs["sampling_topk"] = 0
                kwargs["sampling_temperature"] = temp
            else:
                kwargs["sampling_temperature"] = 0.0

            result = generate(features, [prompt_tokens], **kwargs)[0]
            avg_logprob = result.scores[0]
            if avg_logprob > best_logprob:
                best, best_logprob = result, avg_logprob
            if (
                avg_logprob >= _LOGPROB_THRESHOLD
                or result.no_speech_prob >= _NO_SPEECH_THRESHOLD
            ):
                break

        token_ids = best.sequences_ids[0]

        # skip_special_tokens strips the emitted timestamp tokens from the text.
        text = tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if text:
            texts.append(text)

        if silence_cut:
            # Clean seam: the window already ends in a pause, advance to it.
            seek = cut
        else:
            # Dense speech (or final short chunk): iter_005's last-timestamp
            # rewind, so the next window re-covers any mid-word tail.
            timestamps = [t - timestamp_begin for t in token_ids if t >= timestamp_begin]
            advance_s = float(_WINDOW_SECONDS)
            if len(chunk) >= window and timestamps and timestamps[-1] > 0:
                last_ts = timestamps[-1] * _TIMESTAMP_RESOLUTION
                if _MIN_ADVANCE_SECONDS <= last_ts <= _WINDOW_SECONDS:
                    advance_s = last_ts
            seek += max(int(advance_s * sr), 1)

    return " ".join(texts)
