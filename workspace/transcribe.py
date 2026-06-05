"""Workspace transcribe — autoresearch evolves this file.

REFINE slot on the word-level MBR consensus lineage (iter_011). The parent
decodes each window with beam_size=num_hypotheses=5 and reconciles the N beam
paths position-by-position: beam[0] (the max-joint-logprob path) is the
skeleton, every other beam is aligned to it word-by-word with difflib, and a
beam[0] word is overridden only when a strict majority of the OTHER beams
independently substitute the *same* spelling there. Isolated confidently-wrong
beam[0] words get corrected by the majority; diffusely-disputed words keep the
likelihood prior.

The tune this slot makes targets the dominant substitution axis (57%) through
the lever ledger iter_012 isolated: the MBR vote threshold is cornered, so the
remaining headroom is in *the inputs to the vote* — the beam paths themselves —
not the threshold. iter_009/011 established that across-beam disagreement is
LOCAL and SPARSE (whole-window beams are >=95% char-similar; substitution sits
at isolated word positions). With only N=5 beams there are just 4 non-anchor
voters, so a genuine local substitution position rarely musters the 3-vote
majority the parent requires — the vote fires too seldom to move the axis. This
slot widens the N-best pool to 8 (beam_size=num_hypotheses=8) so the word vote
draws on 7 independent non-anchor voters, and scales the majority threshold
proportionally to 5-of-7. Same arbitration mechanism, denser evidence per
position. iter_012 spent this lever on `patience` (deeper beam); this is the
orthogonal input-improvement — more parallel hypotheses, not deeper ones.

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

# Widened N-best pool feeding the word vote. The parent ran 5/5; iter_009/011
# showed cross-beam disagreement is local and sparse, so 4 non-anchor voters
# rarely reach a 3-vote majority and the vote is near-inert. 8 beams give 7
# non-anchor voters — denser evidence at exactly the isolated substitution
# positions the vote exists to correct.
_BEAM_SIZE = 8
_NUM_HYPOTHESES = 8

# A beam[0] word is overridden only when at least this many of the OTHER beams
# independently align a single identical alternative spelling at that position.
# With N=8 that is 5 of the 7 non-anchor beams — a strict majority scaled from
# the parent's 3-of-4, so beam[0] is still replaced only as the isolated
# outlier, never on a near-even split where the max-joint-logprob prior holds.
_WORD_VOTE_MIN = 5

# Per-token repetition penalty applied DURING decode, before the word vote sees
# the beams. The parent ran the default 1.0, so every one of the 8 beams — the
# anchor and all 7 voters — could lock onto a self-repeating or locally-tempting
# wrong token. iter_016 established ~1.1 as the value that suppresses exactly
# that loop-substitution component of the 57% axis. Cleaning the beams at the
# source gives the consensus vote denser, less-correlated-on-error evidence at
# the isolated substitution positions, rather than voting over paths that all
# inherited the same confident mistake.
_REPETITION_PENALTY = 1.1


def _word_consensus(texts: list[str]) -> str:
    """Word-level minimum-Bayes-risk over the N-best beam list.

    The whole-window MBR (iter008/009) failed because over 30 s of text every
    beam is >=95% char-similar, so the centroid/argmax distinction collapses
    into 3rd-decimal noise. Substitution is *local*: a confidently-wrong
    spelling sits at one word while the rest of the window is identical across
    beams. So keep beam[0] (the max-joint-logprob path) as the skeleton and
    align every other beam to it word-by-word with difflib; at each beam[0]
    word position tally the alternative words the other beams substitute there,
    and override beam[0] only when ``_WORD_VOTE_MIN`` of them independently
    agree on the *same* replacement. An isolated beam[0] outlier is corrected
    by the majority; a word the beams disagree about diffusely keeps beam[0]'s
    likelihood prior.
    """
    if len(texts) <= 1:
        return texts[0] if texts else ""
    base = texts[0].split()
    if not base:
        return texts[0]

    others = [t.split() for t in texts[1:]]
    votes: list[dict[str, int]] = [{} for _ in base]
    for words in others:
        sm = difflib.SequenceMatcher(None, base, words, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "replace" and (i2 - i1) == (j2 - j1):
                for k in range(i2 - i1):
                    w = words[j1 + k]
                    votes[i1 + k][w] = votes[i1 + k].get(w, 0) + 1

    out = list(base)
    for idx, vote in enumerate(votes):
        if not vote:
            continue
        cand, cnt = max(vote.items(), key=lambda kv: kv[1])
        if cnt >= _WORD_VOTE_MIN:
            out[idx] = cand
    return " ".join(out)


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
            num_hypotheses=_NUM_HYPOTHESES,
            sampling_temperature=0.0,
            repetition_penalty=_REPETITION_PENALTY,
        )[0]

        # Reconcile the full N-best beam list at the word level. beam[0] is the
        # skeleton (it carries timestamps and the likelihood prior); the rest
        # vote on local substitutions.
        texts = []
        for seq in res.sequences_ids:
            text_tokens = [t for t in seq if t < timestamp_begin]
            texts.append(
                tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
            )

        merged = _word_consensus(texts)
        if merged:
            pieces.append(merged)

        # Window advance comes from beam[0]'s timestamp tokens (the merged text
        # has none; only the skeleton path carries them).
        token_ids = res.sequences_ids[0]
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
