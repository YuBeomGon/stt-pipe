"""Workspace transcribe — autoresearch evolves this file.

Overlapping chunked decode: split audio into Whisper's native 30-second
windows that overlap by a few seconds, decode each sequentially (batch=1),
then dedup the repeated boundary words and join. Whisper's feature extractor
pads/truncates every call to a fixed 30s window, so a single call drops
everything past the first 30s. Hard, non-overlapping 30s cuts slice
mid-utterance — worst on the near-continuous dense-speech files (longest
speech up to 234s on 0715), where a chunk that *starts* mid-word lacks the
lead-in context Whisper needs and under-emits, the dominant deletion source.
Overlapping the windows makes each boundary appear whole inside one chunk;
the repeated overlap text is removed at the join so it adds no insertions.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's fixed analysis window is 30s; chunk the waveform to match it.
_CHUNK_SECONDS = 30
# Overlap consecutive windows so a word/phrase straddling a 30s boundary is
# captured whole in at least one chunk and the following chunk gets lead-in
# context instead of starting mid-utterance. A 3s overlap still leaves many
# chunks starting mid-word on the dense continuous-speech files (longest speech
# up to ~234s on 0715), the dominant deletion source; widening to 5s gives each
# chunk more lead-in context so it under-emits less. The duplicated overlap text
# is removed at the join by ``_drop_overlap`` so insertions stay flat, and the
# ~8% extra chunks keep runtime within the budget.
_OVERLAP_SECONDS = 5


def _norm(word: str) -> str:
    """Strip surrounding punctuation so the same word transcribed with/without
    trailing punctuation in the two overlapping chunks still compares equal."""
    return word.strip(".,!?…\"'`·:;~()[]").strip()


def _drop_overlap(words: list[str], new_words: list[str], max_overlap: int = 24) -> list[str]:
    """Return ``new_words`` with its leading duplicated overlap removed.

    Find the longest suffix of the accumulated ``words`` that matches a prefix
    of ``new_words`` (compared on punctuation-normalised tokens) and drop that
    prefix — the overlap region the previous chunk already transcribed.
    """
    a = [_norm(w) for w in words]
    b = [_norm(w) for w in new_words]
    limit = min(len(a), len(b), max_overlap)
    for k in range(limit, 0, -1):
        if a[-k:] == b[:k]:
            return new_words[k:]
    return new_words


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    chunk_len = _CHUNK_SECONDS * sr
    if chunk_len <= 0 or audio.shape[0] == 0:
        return ""

    overlap_len = _OVERLAP_SECONDS * sr
    stride = max(chunk_len - overlap_len, 1)

    words: list[str] = []
    for start in range(0, audio.shape[0], stride):
        chunk = audio[start : start + chunk_len]

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        # Every 0715 file is deletion-dominated (length_ratio 0.32–0.81, all
        # < 1.0) while hallucination/insertion stays ~0 — the model under-emits
        # on this long-form conversational audio. length_penalty > 1 biases the
        # beam toward longer hypotheses, directly shrinking deletions; it is
        # inert under greedy decode, so widen the beam to let it take effect.
        results = generate(
            features,
            [prompt_tokens],
            beam_size=5,
            length_penalty=1.5,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text = processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if not text:
            continue

        new_words = text.split()
        if words:
            new_words = _drop_overlap(words, new_words)
        words.extend(new_words)

    return " ".join(words)
