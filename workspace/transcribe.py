"""Workspace transcribe — autoresearch evolves this file.

Discovery (iter 11, explore): the dominant error axis is substitution (57% ≫
del/ins, length_ratio 0.94 healthy). Substitution is by definition a *search*
failure — the decoder reached a frame and committed to the wrong token among
several acoustically-plausible competitors. Every iteration so far decoded with
``beam_size=1`` (greedy): the single most-probable token is taken at each step
with no way to revisit a locally-greedy choice that a later token makes look
wrong. On whisper-large-v3-turbo this hurts more than on full Whisper, because
the distilled turbo decoder commits harder to its top token.

The unused capability is generate()'s N-best return channel. whisper-large-v3-
turbo has only **4 decoder layers** against a **32-layer encoder**; the encoder
(run once per window, the real per-window cost driver) is unchanged by the
beam, so widening it multiplies only the cheap decoder — beam search is
affordable on turbo where it would be ruinous on full Whisper. Setting
``num_hypotheses>1`` makes generate RETURN the whole beam: ``scores`` and
``sequences_ids`` become length-N lists, a return channel every prior iteration
read only at ``[0]`` and discarded the rest of. With the beam in hand the
existing gzip-degeneracy gate can pick the best *clean* hypothesis instead of
blindly trusting the top beam — a looping top-1 no longer wins when a quieter
beam entry is fluent.

The temperature-fallback climb (iter_009) is retained but its greedy rung is
now subsumed by the beam first pass, so the climb only fires when the entire
beam fails the confidence/degeneracy gate. iter_006's silence-aware RMS
windowing and iter_010's confidence-gated <|startofprev|> long-form context
are retained verbatim.

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

# Beam width for the first pass. Affordable here only because turbo's decoder
# is 4 layers (vs 32 encoder layers run once per window). num_hypotheses=N
# makes generate return the full N-best so the degeneracy gate can choose.
_BEAM_SIZE = 5
# Beam-search patience (CT2 generate's beam_search patience factor). Default 1.0
# finalizes a beam as soon as beam_size complete hypotheses exist; >1.0 keeps
# beam_size*patience candidates alive longer before pruning. On the substitution
# axis (a search failure, 57% of errors) this widens the pool of acoustically-
# plausible competitors the degeneracy/log-prob selector chooses among, without
# growing the returned N-best — only the cheap 4-layer decoder does more work.
_PATIENCE = 2.0

# Temperature-fallback schedule (Whisper's native robustness loop). The greedy
# 0.0 rung is dropped: the beam first pass already covers the deterministic
# search far better than greedy did. Climb only when the whole beam failed.
_TEMPERATURES = (0.2, 0.4, 0.6)
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


def _is_better(cand_lp, cand_deg, best_lp, best_deg, have_best):
    """Prefer any non-degenerate hypothesis over a degenerate one; within a
    class break ties by avg log-prob. Same ordering iter_009 used to pick
    across temperatures, now applied across beam hypotheses too."""
    if not have_best:
        return True
    if best_deg and not cand_deg:
        return True
    if best_deg == cand_deg and cand_lp > best_lp:
        return True
    return False


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

        best_token_ids: list[int] = []
        best_logprob = float("-inf")
        best_degenerate = True
        have_best = False
        no_speech_prob = 0.0

        # --- First pass: beam search, reading the WHOLE N-best ----------
        # num_hypotheses=_BEAM_SIZE returns scores/sequences_ids as length-N
        # lists. We score every beam entry through the degeneracy gate and the
        # log-prob tie-break, so a fluent lower-ranked beam can beat a looping
        # top beam — the channel prior iters threw away by reading only [0].
        beam_result = generate(
            features,
            [window_prompt],
            beam_size=_BEAM_SIZE,
            num_hypotheses=_BEAM_SIZE,
            patience=_PATIENCE,
            return_scores=True,
            return_no_speech_prob=True,
        )[0]
        no_speech_prob = beam_result.no_speech_prob
        for hyp_lp, hyp_ids in zip(beam_result.scores, beam_result.sequences_ids):
            text = tokenizer.decode(hyp_ids, skip_special_tokens=True).strip()
            degenerate = _compression_ratio(text) > _COMPRESSION_RATIO_THRESHOLD
            if _is_better(hyp_lp, degenerate, best_logprob, best_degenerate, have_best):
                best_token_ids = hyp_ids
                best_logprob = hyp_lp
                best_degenerate = degenerate
                have_best = True

        confident = (
            best_logprob >= _LOGPROB_THRESHOLD or no_speech_prob >= _NO_SPEECH_THRESHOLD
        )

        # --- Fallback: temperature climb only if the whole beam failed ---
        if not (confident and not best_degenerate):
            for temp in _TEMPERATURES:
                # sampling_topk=0 makes CT2 sample the full distribution so the
                # temperature actually perturbs the decode (topk=1 is greedy).
                result = generate(
                    features,
                    [window_prompt],
                    beam_size=1,
                    sampling_topk=0,
                    sampling_temperature=temp,
                    return_scores=True,
                    return_no_speech_prob=True,
                )[0]
                lp = result.scores[0]
                ids = result.sequences_ids[0]
                text = tokenizer.decode(ids, skip_special_tokens=True).strip()
                degenerate = _compression_ratio(text) > _COMPRESSION_RATIO_THRESHOLD
                if _is_better(lp, degenerate, best_logprob, best_degenerate, have_best):
                    best_token_ids = ids
                    best_logprob = lp
                    best_degenerate = degenerate
                    no_speech_prob = result.no_speech_prob
                    have_best = True

                rung_confident = (
                    lp >= _LOGPROB_THRESHOLD
                    or result.no_speech_prob >= _NO_SPEECH_THRESHOLD
                )
                if rung_confident and not degenerate:
                    break

        token_ids = best_token_ids

        # skip_special_tokens strips the emitted timestamp tokens from the text.
        text = tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if text:
            texts.append(text)

        # Gate the long-form carry: only a confident, non-degenerate window
        # seeds the next window's context (iter_010). A low-confidence or
        # looping decode resets context instead of propagating its
        # substitutions forward. Strip timestamp/special ids (id < <|endoftext|>).
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
