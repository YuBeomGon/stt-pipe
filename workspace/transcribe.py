"""Workspace transcribe — autoresearch evolves this file.

Discovery (iter 9, explore): the decode loop has two accept/quality channels
wired — scores[0] (avg log-prob, iter_005) and no_speech_prob — but neither
sees *degeneracy*. A repetition loop is high-confidence: the decoder is sure of
each repeated token, so scores[0] is large and the iter_005 gate accepts the
window. That is why the two focus files (00003092151…, 02_4038…) trip the
repeated_text guard while their windows pass the log-prob gate, and why
iter_008's global no_repeat_ngram ban — which fired on every window, healthy or
not — lost ground (cer 0.19).

The orthogonal signal is the decoded *text* itself, a return channel only ever
used for the final string: a degenerate window's text is highly repetitive, so
its gzip compression ratio is large. This is Whisper's own native third
robustness trigger (compression_ratio_threshold), and the pipeline omitted it —
iter_005 implemented the temperature-fallback loop with only the log-prob and
no-speech gates.

Mechanism: inside the existing per-window temperature-fallback loop, decode the
candidate text and compute its compression ratio. A window only counts as
"good enough to stop" when it is BOTH confident (or silence) AND non-degenerate
(ratio <= 2.4). A confident-but-looping window is therefore rejected and the
loop climbs temperature; sampling_topk=0 sampling at temp>0 breaks the greedy
loop the way Whisper intends. Among candidates we prefer any non-degenerate
decode over a degenerate one, then break ties by log-prob — so a healthy window
still stops at greedy on the first rung and pays nothing extra. The re-roll is
surgical (only degenerate windows climb), unlike iter_008's blanket constraint.

iter_006's silence-aware RMS windowing is retained verbatim.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import gzip

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
# Whisper's native degeneracy gate: text whose gzip ratio exceeds this is
# repetitive enough to be treated as a failed decode and re-rolled.
_COMPRESSION_RATIO_THRESHOLD = 2.4


def _compression_ratio(text: str) -> float:
    """gzip compression ratio of the decoded text (Whisper's degeneracy proxy).

    A looping decode compresses far better than natural speech, so a high ratio
    flags repetition that the log-prob gate cannot see.
    """
    payload = text.encode("utf-8")
    if not payload:
        return 0.0
    return len(payload) / len(gzip.compress(payload))


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

        # Temperature fallback with a degeneracy gate. A window stops the climb
        # only when it is confident (or silence) AND non-repetitive; a
        # high-logprob loop is rejected so the next, higher temperature can
        # break it. scores[0], no_speech_prob and the decoded text are the
        # three signals consulted.
        best = None
        best_logprob = float("-inf")
        best_degenerate = True
        best_token_ids = []
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
            token_ids = result.sequences_ids[0]
            text = tokenizer.decode(token_ids, skip_special_tokens=True).strip()
            degenerate = _compression_ratio(text) > _COMPRESSION_RATIO_THRESHOLD

            # Prefer any non-degenerate decode over a degenerate one; break ties
            # within a class by log-prob.
            better = (
                best is None
                or (best_degenerate and not degenerate)
                or (best_degenerate == degenerate and avg_logprob > best_logprob)
            )
            if better:
                best = result
                best_logprob = avg_logprob
                best_degenerate = degenerate
                best_token_ids = token_ids

            confident = (
                avg_logprob >= _LOGPROB_THRESHOLD
                or result.no_speech_prob >= _NO_SPEECH_THRESHOLD
            )
            if confident and not degenerate:
                break

        token_ids = best_token_ids

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
