"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. The whole job's consensus machinery (word-level MBR, iter_011 /
024-028) voted position-wise over the **beam** N-best, and the ledger proved why
it stayed inert: cross-beam disagreement is LOCAL and SPARSE — the beams are
>=95% char-similar (iter_009/011), so they are correlated *on their errors*.
A confident-wrong substitution sits in beam[0] AND most of its neighbours, so no
position-wise vote can outvote it. iter_024 named the binding constraint exactly:
the vote's **signal density**, not its threshold.

This iteration attacks that root cause with a structurally different decode:
**Minimum-Bayes-Risk decoding over INDEPENDENT temperature samples.** Instead of
one beam search returning correlated hypotheses, each window is decoded with
``num_hypotheses=N`` random samples drawn at ``sampling_temperature>0`` (CT2
enables true sampling once ``sampling_topk != 1``). Independent draws from the
decoder's full distribution are diverse where beams are not: a substitution that
is a *confident* mode survives in every sample, but a substitution that is merely
the locally-tempting-but-uncertain choice (the phone-band obstruent confusions
that make up the 57% substitution axis — high-frequency cues attenuated by the
300-3400 Hz telephony band, so the posterior there is flat) lands differently in
each draw and is therefore a minority across the sample cloud.

The selection rule is the MBR objective itself: pick the sample that minimises
expected character error against the rest of the cloud — i.e. the **medoid**, the
draw with maximum summed ``difflib`` char-similarity to the other samples. This
is consensus by *whole-sequence* central tendency, not by per-position majority,
so it needs no alignment and no vote threshold (the two things that made the
beam-MBR brittle). A random per-sample substitution increases that sample's
distance to the cluster and loses; the central, agreed-upon transcription wins.

The score channel (``return_scores``) breaks ties when the cloud is degenerate
(every sample near-identical → similarities all ~1.0): the higher-avg-logprob
draw is preferred. This is genuinely new on this job — every prior consensus
used the beam N-best; none ever drew independent samples or selected by an
edit-distance Bayes risk.

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

# MBR-over-samples parameters. N independent draws give the consensus its
# diversity (the signal density the correlated beam N-best lacked, ledger
# iter_024). The temperature must be high enough that uncertain substitutions
# scatter across draws, low enough that confident speech stays stable;
# Whisper's own fallback ladder treats ~0.4 as the first genuinely-sampling
# rung. sampling_topk=0 samples from the full softmax (CT2 enables sampling as
# soon as sampling_topk != 1).
_NUM_SAMPLES = 5
_SAMPLING_TEMPERATURE = 0.4
_SAMPLING_TOPK = 0

# Gentle token-level repetition penalty (kept from the best lineage, iter_016):
# suppresses the self-repeating degenerate path one token at a time inside each
# sampled draw before the medoid even arbitrates.
_REPETITION_PENALTY = 1.1


def _similarity(a: str, b: str) -> float:
    # Character-level overlap proxy for 1 - normalised edit distance; stdlib,
    # no external Levenshtein dependency. SequenceMatcher.ratio() is the MBR
    # risk kernel: higher = lower expected char error against this reference.
    return difflib.SequenceMatcher(None, a, b).ratio()


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

        # Draw N independent samples from the decoder's distribution in a single
        # generate() call (num_hypotheses=N with sampling enabled). These are
        # genuinely diverse, unlike the >=95%-similar beam N-best.
        res = generate(
            features,
            [sot_tokens],
            beam_size=1,
            num_hypotheses=_NUM_SAMPLES,
            sampling_temperature=_SAMPLING_TEMPERATURE,
            sampling_topk=_SAMPLING_TOPK,
            repetition_penalty=_REPETITION_PENALTY,
            return_scores=True,
        )[0]

        seqs = res.sequences_ids
        scores = res.scores if res.scores else [0.0] * len(seqs)

        # Decode every sample to text (timestamp tokens stripped for the
        # similarity comparison; kept separately on the chosen draw for advance).
        texts = []
        for token_ids in seqs:
            text_tokens = [t for t in token_ids if t < timestamp_begin]
            texts.append(
                tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
            )

        # MBR selection: the medoid sample — maximum summed char-similarity to
        # the rest of the cloud (minimum expected char error). Score channel
        # breaks ties when the cloud is degenerate / near-identical.
        best_idx = 0
        best_key = (float("-inf"), float("-inf"))
        for i, ti in enumerate(texts):
            risk = sum(_similarity(ti, tj) for j, tj in enumerate(texts) if j != i)
            key = (risk, scores[i])
            if key > best_key:
                best_key = key
                best_idx = i

        chosen_ids = seqs[best_idx]
        ts_tokens = [t for t in chosen_ids if t >= timestamp_begin]

        text = texts[best_idx]
        if text:
            pieces.append(text)

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
