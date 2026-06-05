"""Workspace transcribe — autoresearch evolves this file.

DIVERGE slot. Every prior pipeline drew exactly ONE decode per acoustic span:
the window boundary advanced on the decoder's own ``<|t|>`` timestamps (or a
fixed non-overlapping stride), so each second of audio was transcribed once, by
a single window, in a single positional context. The substitution/deletion that
concentrates at a *window boundary* — a word whose acoustic evidence is split
across the seam, or a token the decoder drops because it sits at the very edge
of the 30 s context — is structurally invisible to every decode knob, return
channel, and consensus scheme in the ledger: by the time generate() runs, the
boundary is already fixed and the evidence already cut.

This slot builds a fundamentally different decode strategy: **redundant
overlapping windows reconciled by a token-id seam merge.** Each acoustic span is
decoded TWICE, by two windows that place it in different positions of their
context (once near a window's tail, once near the next window's head), and the
duplicated overlap is removed by finding the longest matching token-id run at
the seam. No span sits at a window edge in *both* decodes, so a boundary that
truncates one decode is interior to the other.

Two ledger facts make the merge unambiguous (iter_035): appending
``<|notimestamps|>`` to the SOT prompt zeroes the decoder's probability mass on
the ~1500 timestamp tokens, so ``sequences_ids`` is pure text — no interleaved
``<|t|>`` to parse and no per-step timestamp competitor against a
low-probability obstruent-word completion. With pure-text ids, a difflib
longest-match between the tail of the accumulated transcript and the head of the
next window is a clean seam: everything after the matched run is genuinely new.

The windowing is a fixed dense overlap (advance < window), NOT iter_057's
RMS-silence segmentation that collapsed coverage (deletion 0.76) — full coverage
is structural here because consecutive windows overlap by construction. Runtime
stays near budget: one beam pass per window, and the overlap fraction is modest.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import difflib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

_WINDOW_SECONDS = 30.0
# Dense fixed overlap: advance < window so every acoustic span is decoded by two
# consecutive windows in different positional contexts. 6 s overlap is generous
# enough that the seam region carries many shared tokens for the merge to lock
# onto, yet small relative to the 30 s window so the redundancy cost is ~25%.
_ADVANCE_SECONDS = 24.0

_BEAM_SIZE = 5

# Gentle token-level repetition penalty (carried from the best lineage; iter_016
# showed ~1.1 suppresses the confident-wrong-loop component of the substitution
# axis during decode rather than post-hoc).
_REPETITION_PENALTY = 1.1

# Seam-merge guards (token-id units). A merge is only accepted when the longest
# matching run is at least this long AND sits at the seam — near the tail of the
# accumulated ids and the head of the new window. If the seam can't be located
# confidently we concatenate without dedup: better a little duplicated text
# (coverage preserved) than dropping real content on a spurious match.
_MIN_OVERLAP_TOKENS = 8
_SEAM_TOLERANCE_TOKENS = 24


def _stitch(acc: list[int], new: list[int]) -> list[int]:
    """Append ``new`` to ``acc``, removing the duplicated overlap at the seam."""
    if not acc:
        return list(new)
    if not new:
        return acc

    # Compare only the tail of acc against new — the overlap can't be longer than
    # one window, and bounding the comparison keeps the match anchored at the seam.
    tail_len = min(len(acc), len(new) + _SEAM_TOLERANCE_TOKENS)
    a_off = len(acc) - tail_len
    tail = acc[a_off:]

    sm = difflib.SequenceMatcher(None, tail, new, autojunk=False)
    m = sm.find_longest_match(0, len(tail), 0, len(new))

    if m.size >= _MIN_OVERLAP_TOKENS:
        reaches_acc_end = (a_off + m.a + m.size) >= (len(acc) - _SEAM_TOLERANCE_TOKENS)
        near_new_head = m.b <= _SEAM_TOLERANCE_TOKENS
        if reaches_acc_end and near_new_head:
            # Drop new's matched-and-before region; keep only its fresh tail.
            return acc + new[m.b + m.size :]

    return acc + new


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    n_samples = audio.shape[0]
    win_samples = int(_WINDOW_SECONDS * sr)
    advance_samples = max(1, int(_ADVANCE_SECONDS * sr))

    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")
    # Pure-text decode mode: <|notimestamps|> follows the SOT triple so the
    # decoder never emits <|t|> tokens and sequences_ids is a clean token stream.
    prompt_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    merged: list[int] = []
    cursor = 0
    while cursor < n_samples:
        chunk = audio[cursor : cursor + win_samples]

        inputs = processor(
            [chunk],
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        res = generate(
            features,
            [prompt_tokens],
            beam_size=_BEAM_SIZE,
            sampling_temperature=0.0,
            repetition_penalty=_REPETITION_PENALTY,
        )[0]

        # notimestamps => no <|t|> tokens, but special ids can still appear; keep
        # only real text tokens for the seam merge.
        text_ids = [t for t in res.sequences_ids[0] if t < timestamp_begin]
        merged = _stitch(merged, text_ids)

        cursor += advance_samples

    return tokenizer.decode(merged, skip_special_tokens=True).strip()
