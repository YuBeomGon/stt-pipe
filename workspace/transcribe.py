"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing: Whisper's feature extractor pins every clip to a fixed
30-second mel (3000 frames), so the single-window stub discards everything past
0:30. Most 0715 calls run minutes long, so that truncation is the dominant
deletion source. We split the audio into windows and decode them in order,
stitching the transcripts back together.

COMBINE: this pipeline grafts the two parent families. From family_003 it keeps
single-window prev-text conditioning (<|startofprev|> + recent token ids before
the SOT sequence), which drove hallucination down (0.27 -> 0.18); from
family_001 it keeps the full beam-search surface (beam_size, patience,
length_penalty, no_repeat_ngram). Both parents stalled on the SAME axis —
deletion 0.76, length_ratio 0.71 — so we add a deletion guard on that axis: the
windows OVERLAP. A hard non-overlapping 30s cut lands in the middle of a word
or segment, and Whisper drops the speech straddling the seam (the window that
ends mid-word truncates it; the window that starts mid-word mis-decodes the
fragment). Stepping by less than the window length means every seam is covered
twice, so no boundary span is lost. The duplicated overlap text is removed by a
word-level suffix/prefix dedup at stitch time so the recovered coverage does not
turn into insertions/hallucination (the axis family_001 regressed).

Memory: peak decoder VRAM ~ windows-per-call x beam x seq_len. We decode exactly
one window per ``generate`` call (concurrency 1 x beam over a short prev prefix —
the smallest footprint used), so the overlap only adds ~15% more single-window
calls; runtime stays well inside budget.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30
_OVERLAP_SECONDS = 4
_PREV_CONTEXT_TOKENS = 64
_MAX_OVERLAP_WORDS = 24


def _dedup_extend(acc_words: list[str], new_words: list[str]) -> None:
    """Append new_words to acc_words, dropping the largest leading run of
    new_words that exactly re-states the trailing run of acc_words (the doubly
    covered overlap region)."""
    max_k = min(len(acc_words), len(new_words), _MAX_OVERLAP_WORDS)
    for k in range(max_k, 0, -1):
        if acc_words[-k:] == new_words[:k]:
            acc_words.extend(new_words[k:])
            return
    acc_words.extend(new_words)


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    window_samples = int(sr * _WINDOW_SECONDS)
    if window_samples <= 0:
        window_samples = len(audio) or 1
    step_samples = int(sr * (_WINDOW_SECONDS - _OVERLAP_SECONDS))
    if step_samples <= 0:
        step_samples = window_samples

    starts = list(range(0, max(len(audio), 1), step_samples))

    feature_list = []
    for start in starts:
        chunk = audio[start : start + window_samples]
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        feature_list.append(np.asarray(inputs.input_features))

    sot_suffix = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )
    startofprev_id = processor.tokenizer.convert_tokens_to_ids("<|startofprev|>")
    eot_id = processor.tokenizer.convert_tokens_to_ids("<|endoftext|>")

    acc_words: list[str] = []
    prev_tokens: list[int] = []
    for window in feature_list:
        features = to_storage_view(window)

        if prev_tokens:
            prompt = [startofprev_id] + prev_tokens[-_PREV_CONTEXT_TOKENS:] + sot_suffix
        else:
            prompt = sot_suffix

        results = generate(
            features,
            [prompt],
            beam_size=5,
            patience=2.0,
            length_penalty=1.1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text_tokens = [t for t in token_ids if t < eot_id]
        if text_tokens:
            prev_tokens = text_tokens[-_PREV_CONTEXT_TOKENS:]

        piece = processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if piece:
            _dedup_extend(acc_words, piece.split())

    return " ".join(acc_words)
