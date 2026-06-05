"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. This replaces the whole decode/selection mechanism with a
cross-sample **ROVER token-vote fusion**, attacking the dominant 57%
substitution axis at the one place every prior iteration left untouched.

The ledger's binding facts about that axis:
  - the beam N-best is >=95% correlated (iter_063), so a confidently-wrong
    phone-band domain token sits in *all* beam hypotheses — beam width,
    patience, and lexicon-rerank (063-085) provably cannot surface the correct
    token because it never enters the beam;
  - align()-driven excision (iter_081-085) can only *delete* a wrong token,
    converting a substitution to a deletion with no net CER gain (iter_082).

The unused capability: **temperature sampling decorrelates errors across
draws**. A pool of independent random samples (whisper's `best_of`) is not
locked to one beam path, so the correct obstruent-attenuated domain token the
beam pruned can appear in a *minority* of the samples. Position-wise majority
voting — ROVER, structurally distinct from every prior whole-hypothesis MBR /
medoid / sequence-rescore selector, which choose one entire hypothesis and so
inherit its shared substitution — can then *repair* the anchor token by swapping
it for the cross-sample consensus token.

Mechanism per window:
  - decode a precision **anchor**: T=0 beam search (also the source of the
    timestamp tokens that drive the advance, unchanged from the incumbent);
  - decode a **diverse pool**: K random samples from the same encode
    (beam_size=1, sampling_temperature>0, top-k);
  - align each sample's text token-ids to the anchor's with difflib and tally,
    per anchor position, which token ids the samples propose;
  - at each 1:1-aligned position, replace the anchor token with a pool token
    only when a strict majority of samples agree on it AND that agreement
    exceeds the samples backing the anchor token.

Insert/delete and length-mismatched spans contribute no votes, so the fused
sequence has exactly the anchor's length: coverage (length_ratio 0.96) and the
insertion axis are left untouched and only the substitution axis is moved.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import difflib
from collections import Counter

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

_WINDOW_SECONDS = 30.0
_TIME_PRECISION = 0.02
_MIN_ADVANCE_SECONDS = 2.0

_BEAM_SIZE = 5

# Diverse sampling pool (whisper's best_of). Random sampling decorrelates errors
# across draws, unlike the >=95%-correlated beam N-best (ledger iter_063), so a
# correct phone-band domain token the beam pruned can surface in the pool and be
# voted in. Temperature/top-k set the pool's entropy. iter_086/087 ran T=0.4,
# topk=10 and substitution stayed pinned at 0.56: at that low temperature each
# sample sits too close to the greedy anchor's beam path, so the pool echoes the
# anchor token rather than carrying the beam-pruned correct one — the
# decorrelation premise barely holds and the vote has nothing better to swap in.
# Raise temperature to 0.7 so draws genuinely escape the anchor path, and narrow
# top-k to 6 so those escaped draws still concentrate on a small plausible set —
# a 4/5 supermajority can only form if the freed samples converge, which a wide
# top-k at high temperature would scatter into noise.
_NUM_SAMPLES = 5
_SAMPLE_TEMPERATURE = 0.7
_SAMPLE_TOPK = 6

# ROVER override gate: a pool token may replace the anchor token at a
# 1:1-aligned position only if at least this many samples agree on it AND that
# agreement strictly exceeds the samples backing the anchor token. iter_086 ran
# a bare 3/5 majority and its swaps were net-harmful (sub only 0.57->0.56 while
# hal rose 0.00->0.18) — a phonetic-neighbour the T=0.4 pool happens to agree on
# overrode a correct anchor token. A 4/5 SUPERMAJORITY admits only the highest-
# confidence cross-sample repairs; every weaker position falls back to the strong
# precision anchor, so the fusion stops trading the substitution axis for noise.
_VOTE_MIN = 4


def _rover_fuse(anchor: list[int], samples: list[list[int]]) -> list[int]:
    """Position-wise majority vote of the sample pool onto the anchor tokens.

    Only 1:1-aligned spans (difflib ``equal``/``replace`` of equal length)
    contribute votes, so the returned sequence keeps the anchor's length — the
    insertion/deletion axes cannot move, only substitution.
    """
    if not anchor or not samples:
        return anchor

    votes = [Counter() for _ in anchor]
    for seq in samples:
        if not seq:
            continue
        matcher = difflib.SequenceMatcher(a=anchor, b=seq, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("equal", "replace") and (i2 - i1) == (j2 - j1):
                for off in range(i2 - i1):
                    votes[i1 + off][seq[j1 + off]] += 1

    fused: list[int] = []
    for pos, tok in enumerate(anchor):
        counter = votes[pos]
        if counter:
            alt, alt_n = counter.most_common(1)[0]
            if alt != tok and alt_n >= _VOTE_MIN and alt_n > counter.get(tok, 0):
                tok = alt
        fused.append(tok)
    return fused


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

        inputs = processor([chunk], sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        # Precision anchor: T=0 beam decode. Its timestamp tokens drive advance.
        anchor_res = generate(
            features,
            [sot_tokens],
            beam_size=_BEAM_SIZE,
            sampling_temperature=0.0,
        )[0]

        # Diverse pool: K decorrelated random samples from the same encode.
        sample_res = generate(
            features,
            [sot_tokens],
            beam_size=1,
            num_hypotheses=_NUM_SAMPLES,
            sampling_temperature=_SAMPLE_TEMPERATURE,
            sampling_topk=_SAMPLE_TOPK,
        )[0]

        anchor_ids = anchor_res.sequences_ids[0]
        anchor_text = [t for t in anchor_ids if t < timestamp_begin]
        ts_tokens = [t for t in anchor_ids if t >= timestamp_begin]

        sample_texts = [
            [t for t in seq if t < timestamp_begin]
            for seq in sample_res.sequences_ids
        ]

        fused = _rover_fuse(anchor_text, sample_texts)

        text = tokenizer.decode(fused, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
