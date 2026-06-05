"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prior iteration read at most ``sequences_ids[0]`` — the
single highest-likelihood beam path — and arbitrated *between windows*
(temperature fallback, no_speech gating) but never *within* a window's beam.
The decode call carries an N-best channel no iteration has ever populated:
``generate(..., num_hypotheses=N)`` fills ``res.sequences_ids[0..N-1]`` with
the full top-N beam list, not just the winner.

The incumbent's failure axis is substitution (56% ≫ del/ins, coverage healthy
at length_ratio 0.96): the headroom is in *which* token the decoder commits to
on low-confidence phone-band windows, not in coverage. Beam[0] is the path of
maximum joint log-prob — but on narrowband call-center audio that maximum is
often a confidently-wrong spelling one beam path locks onto, while the *other*
beams cluster around the correct word.

This slot replaces single-path selection with **Minimum-Bayes-Risk consensus
decoding** over the N-best list. We decode N hypotheses per window, then emit
not the top-likelihood path but the hypothesis with minimum expected character
risk against the rest — i.e. the one most similar to all the others, the
centroid of the beam. An isolated wrong spelling has high risk (it disagrees
with the consensus) and is rejected; a spelling several beams independently
agree on has low risk and wins. This attacks substitution directly: instead of
trusting a single argmax path, it lets the beam vote on *what was said*.

This is a fundamentally different decode strategy than the incumbent's
single-path temperature-fallback gate — arbitration moves from between-window
confidence thresholds to within-window cross-hypothesis agreement, and reads a
return channel (the N-best list) every prior iter threw away by taking only
``sequences_ids[0]``.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import difflib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

_WINDOW_SECONDS = 30.0
_TIME_PRECISION = 0.02
_MIN_ADVANCE_SECONDS = 2.0

# Beam search returns its top-N paths when num_hypotheses>1. We score all N
# against each other (MBR) rather than trusting the single argmax path.
_BEAM_SIZE = 5
_NUM_HYPOTHESES = 5


def _consensus_index(texts: list[str]) -> int:
    """Index of the minimum-Bayes-risk hypothesis.

    Risk of hypothesis i = sum_j (1 - char_similarity(i, j)); minimising it is
    equivalent to maximising total similarity to the rest of the beam. The
    centroid hypothesis — the spelling the most beam paths agree on — wins,
    so an isolated confidently-wrong path is rejected.
    """
    n = len(texts)
    if n <= 1:
        return 0
    best_i = 0
    best_score = -1.0
    for i in range(n):
        score = 0.0
        for j in range(n):
            if i == j:
                continue
            score += difflib.SequenceMatcher(None, texts[i], texts[j]).ratio()
        if score > best_score:
            best_score = score
            best_i = i
    return best_i


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

        # Populate the N-best channel: beam search returns its top-N full paths
        # in sequences_ids[0..N-1] (every prior iter read only [0]).
        res = generate(
            features,
            [sot_tokens],
            beam_size=_BEAM_SIZE,
            num_hypotheses=_NUM_HYPOTHESES,
            sampling_temperature=0.0,
        )[0]

        hyps = res.sequences_ids
        texts: list[str] = []
        for token_ids in hyps:
            text_tokens = [t for t in token_ids if t < timestamp_begin]
            texts.append(
                tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
            )

        # MBR consensus: emit the centroid hypothesis, not beam[0].
        idx = _consensus_index(texts)
        chosen_ids = hyps[idx]
        ts_tokens = [t for t in chosen_ids if t >= timestamp_begin]
        text = texts[idx]
        if text:
            pieces.append(text)

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
