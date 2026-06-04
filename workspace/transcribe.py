"""Workspace transcribe — autoresearch evolves this file.

Discovery (iter 10, explore): the dominant error axis is substitution (56% ≫
del/ins, length_ratio 0.94 healthy), so the headroom is in *what* gets
mis-recognized — domain-term consistency — not coverage. Each window currently
decodes lexically blind: the prompt is a constant [SOT, ko, transcribe] prefix,
so the same insurance term decoded correctly in one window can be substituted
differently in the next, with no memory between them.

The unused capability is Whisper's native long-form anti-drift: the flat prompt
list (probed iter_003) accepts [<|startofprev|>, *prev_text_ids, *sot] to carry
the previous window's decoded text forward as decoder context. iter_003 fed
that channel blindly and compounded its own substitutions window-to-window (cer
0.49); iter_007's static glossary could not carry *real* decoded context. The
missing piece both lacked is a GATE: the scores[0] log-prob channel and the
gzip-degeneracy signal (both already wired here) decide whether a window's text
is trustworthy enough to condition the next one on.

Mechanism: maintain a rolling `context_ids` of the prior window's decoded text
tokens (timestamps/specials stripped, capped to the last 200 — Whisper's own
half-context limit). Prepend [<|startofprev|>, *context_ids, *sot] when context
exists. After each window, carry its text forward ONLY if it cleared the -1.0
log-prob gate AND was non-degenerate; otherwise reset context to empty. The gate
is exactly what iter_003 omitted — a low-confidence or looping decode can no
longer poison its successors, while confident windows propagate consistent
domain spellings. No extra decode passes, so the runtime budget is unchanged.

iter_006's silence-aware RMS windowing and iter_009's temperature/degeneracy
fallback loop are retained verbatim.

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
# Max prior-window text tokens carried as <|startofprev|> context. Whisper caps
# its own long-form context at half the 448-token window; mirror that bound so
# the prompt cannot grow without limit and crowd out the new audio.
_MAX_CONTEXT_TOKENS = 200


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
    sot_prompt = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")
    # Long-form context channel: <|startofprev|> prefixes prior text; <|endoftext|>
    # marks the boundary between natural-text ids (below it) and the special/
    # timestamp ids (at/above it) we must strip before carrying context forward.
    startofprev = tokenizer.convert_tokens_to_ids("<|startofprev|>")
    eot = tokenizer.convert_tokens_to_ids("<|endoftext|>")

    texts = []
    context_ids: list[int] = []
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

        # Carry the prior confident window's text as decoder context (native
        # long-form conditioning); cold-start prompt when there is none.
        if context_ids:
            window_prompt = [startofprev, *context_ids[-_MAX_CONTEXT_TOKENS:], *sot_prompt]
        else:
            window_prompt = list(sot_prompt)

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

            result = generate(features, [window_prompt], **kwargs)[0]
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

        # Gate the long-form carry: only a confident, non-degenerate window
        # seeds the next window's context. This is the check iter_003 lacked —
        # a low-confidence or looping decode resets context instead of
        # propagating its substitutions forward. Strip timestamp/special ids,
        # keeping only natural text tokens (id < <|endoftext|>).
        if best_logprob >= _LOGPROB_THRESHOLD and not best_degenerate:
            context_ids = [t for t in token_ids if t < eot]
        else:
            context_ids = []

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
