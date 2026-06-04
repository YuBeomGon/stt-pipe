"""Workspace transcribe — autoresearch evolves this file.

Coverage stayed low (length_ratio ≈ 0.66, deletion ≈ 82%) even though every
second of audio is fed to the model — iter3 localized the loss to *inside* the
30s windows: each window decodes but stops part-way through, so the tail of the
window is silently dropped as deletion.

Every iteration so far hard-coded ``<|notimestamps|>`` into the decoder prompt,
so the decode has NEVER run in Whisper's timestamped regime. notimestamps
decode is prone to early-EOS on long dense segments; the timestamp-trained
decode is built to walk segment-by-segment to the window end, which directly
attacks the in-window deletion. Timestamps also tell us *where* the model
stopped, so instead of a blind 30s hop we advance the cursor to the last
emitted timestamp — a window that truncated early gets its dropped tail
re-decoded with fresh context rather than lost. This is the standard sequential
long-form algorithm, only possible once timestamps are enabled.

iter6 (explore): coverage is now healthy (length_ratio 0.95) and the dominant
error axis flipped to *substitution* (57%) — local mis-recognition of domain /
phone-band audio, the exact failure greedy decoding locks into by committing to
the single highest-prob token at each step. Every iteration so far decoded
greedily (``beam_size=1``) and read only ``sequences_ids[0]``, silently
discarding the rest of what ``generate()`` returns. With ``beam_size>1`` /
``num_hypotheses>1`` the call returns a *ranked N-best list* (parallel
``sequences_ids`` / ``scores``) — a capability greedy never populates and we
have never had. We enable beam search (turbo's 4-layer decoder keeps it cheap)
and consume the N-best: among hypotheses whose score is within a small margin
of the best, we keep the one reaching furthest into the window, so beam search
attacks substitution while the completeness tie-break guards coverage without
rewarding hallucinated length.

iter11 (repair): iter10 tried to rerank the score-margin N-best by acoustic
confidence from ``model.align()`` — a real lever (align's ``text_token_probs``
is a per-token acoustic posterior the beam's LM-blended ``scores`` cannot
isolate, the substitution axis) but it crashed: ``align()`` requires
``features.batch == len(text_tokens)`` and iter10 passed one 30s window against
an N-hypothesis text batch (``MatMul: batch dimension ... should match``). The
fix is to call ``align()`` once per hypothesis (batch 1, the faster-whisper
contract) instead of one batched call. We keep the completeness tie-break as the
fallback when fewer than two hypotheses carry alignable text.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
# Whisper timestamp tokens are spaced 0.02s apart starting at <|0.00|>.
_TS_STEP = 0.02
# If the last timestamp lands before this, treat the window as having no usable
# continuation point and hop a full window — bounds total window count and
# guarantees the cursor always advances (loop termination).
_MIN_ADVANCE = 5.0
# Beam width / N-best depth. Beam search (vs greedy) lets the decoder recover
# from a locally-wrong token instead of committing to it — the substitution
# fix. Depth 5 is the Whisper default and cheap on the 4-layer turbo decoder.
_BEAM_SIZE = 5
_NUM_HYPOTHESES = 5
# Hypotheses whose avg log-prob is within this margin of the best are treated as
# confidence-equivalent; an acoustic-confidence rerank then decides between them
# (completeness is the fallback when alignment isn't applicable).
_SCORE_MARGIN = 0.05
# Whisper's encoder emits ~50 output frames per second (1500 for a 30s window);
# align() needs the valid (non-padded) frame count for the chunk.
_FRAMES_PER_SEC = 50
_MAX_FRAMES = _WINDOW_SECONDS * _FRAMES_PER_SEC


def _last_ts(token_ids, ts_begin):
    """Last timestamp (seconds) the hypothesis reached, or None if it has none."""
    for tid in reversed(token_ids):
        if tid >= ts_begin:
            return (tid - ts_begin) * _TS_STEP
    return None


def _text_ids(token_ids, eot_id):
    """Text (BPE) token ids only — drop timestamp/special/eos tokens.

    Whisper text tokens occupy ids below <|endoftext|>; sot/lang/task/timestamp
    tokens all sit at or above it. align() conditions on text tokens only.
    """
    return [t for t in token_ids if t < eot_id]


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    window = _WINDOW_SECONDS * sr
    n = len(audio)

    # Prompt WITHOUT <|notimestamps|> -> the decoder emits timestamp tokens.
    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    # Any token id >= this is a timestamp token (<|0.00|> .. <|30.00|>);
    # everything below (text, eos, control) is not.
    ts_begin = processor.tokenizer.convert_tokens_to_ids("<|0.00|>")
    # <|endoftext|> bounds the text-token id range: BPE text tokens are below it,
    # all special/timestamp tokens at or above. Used to feed align() text only.
    eot_id = processor.tokenizer.convert_tokens_to_ids("<|endoftext|>")

    segments = []
    pos = 0
    while pos < n:
        chunk = audio[pos : pos + window]
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        result = generate(
            features,
            [prompt_tokens],
            beam_size=_BEAM_SIZE,
            num_hypotheses=_NUM_HYPOTHESES,
            return_scores=True,
            sampling_temperature=0.0,
            no_repeat_ngram_size=3,
        )[0]

        # generate() returns a ranked N-best list (sequences_ids / scores are
        # parallel). Restrict to hypotheses within _SCORE_MARGIN of the top
        # score, then rerank those by mean ACOUSTIC confidence from align():
        # the beam score blends acoustic likelihood with the LM prior, so a
        # fluent-but-wrong hypothesis can outrank the acoustically-faithful one,
        # which is exactly how substitutions survive beam search.
        hyps = result.sequences_ids
        scores = result.scores or [0.0] * len(hyps)
        best_score = max(scores)
        contenders = [
            h for h, s in zip(hyps, scores) if s >= best_score - _SCORE_MARGIN
        ]

        if len(contenders) >= 2:
            # align() requires features.batch == len(text_tokens); iter10 crashed
            # passing one window against an N-hypothesis text batch. Call it once
            # per hypothesis (batch 1) and rerank by mean acoustic posterior.
            num_frames = max(
                1, min(_MAX_FRAMES, int(round(len(chunk) / sr * _FRAMES_PER_SEC)))
            )
            scored = []
            for h in contenders:
                text_only = _text_ids(h, eot_id)
                if not text_only:
                    continue
                aligned = model.align(
                    features,
                    prompt_tokens,
                    [text_only + [eot_id]],
                    num_frames,
                )[0]
                probs = aligned.text_token_probs
                if probs:
                    scored.append((sum(probs) / len(probs), h))

            if len(scored) >= 2:
                token_ids = max(scored, key=lambda x: x[0])[1]
            else:
                # Fallback: completeness tie-break — furthest-reaching hypothesis.
                token_ids = max(
                    contenders, key=lambda h: (_last_ts(h, ts_begin) or 0.0)
                )
        else:
            token_ids = contenders[0]

        text = processor.tokenizer.decode(
            token_ids, skip_special_tokens=True
        ).strip()
        if text:
            segments.append(text)

        # Advance the cursor to the last timestamp the model actually reached,
        # so an early-truncated window re-decodes its dropped tail next pass.
        last_ts = _last_ts(token_ids, ts_begin)

        if last_ts is not None and last_ts >= _MIN_ADVANCE:
            advance = min(last_ts, _WINDOW_SECONDS)
        else:
            advance = _WINDOW_SECONDS
        pos += int(advance * sr)

    return " ".join(segments)
