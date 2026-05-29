"""Workspace transcribe — autoresearch evolves this file.

Silence-snapped overlapping chunked decode: split audio into Whisper's native
30-second windows, but place each window's start at the quietest point near the
nominal stride instead of on a fixed grid, decode each sequentially (batch=1),
then dedup the repeated boundary words and join. Whisper's feature extractor
pads/truncates every call to a fixed 30s window, so a single call drops
everything past the first 30s. Fixed-grid 25s strides land boundaries at
arbitrary points — frequently mid-utterance on the near-continuous dense-speech
files (longest speech up to 234s on 0715), where a chunk that *starts* mid-word
lacks the lead-in context Whisper needs and under-emits; that under-emission is
the dominant deletion source (every 0715 file has length_ratio < 1.0 with
hallucination/insertion ~0). Snapping each boundary to a nearby low-energy frame
makes chunks begin in a natural pause with a clean onset, so the model stops
dropping the lead-in of each window. The search window is centred on the nominal
stride and only a couple seconds wide, so the chunk count — and therefore the
runtime — stays essentially unchanged, and the variable overlap is still removed
at the join by ``_drop_overlap``.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's fixed analysis window is 30s; chunk the waveform to match it.
_CHUNK_SECONDS = 30
# Overlap consecutive windows so a word/phrase straddling a boundary is captured
# whole in at least one chunk and the following chunk gets lead-in context
# instead of starting mid-utterance. The duplicated overlap text is removed at
# the join by ``_drop_overlap``.
_OVERLAP_SECONDS = 5
# Half-width of the window (seconds) searched around each nominal boundary for
# the quietest frame to snap to. Kept small so chunk starts stay near the
# nominal stride: the chunk count, and thus the runtime, barely moves, while the
# boundary still lands in a pause rather than mid-word. The ~±2s wobble in
# stride keeps the overlap within the few-words range ``_drop_overlap`` handles.
_BOUNDARY_SEARCH_SECONDS = 2.0
# Frame length (seconds) for the coarse RMS energy used to locate pauses.
_ENERGY_FRAME_SECONDS = 0.03


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


def _quiet_boundary(audio: np.ndarray, center: int, radius: int, frame: int) -> int:
    """Return the sample index of the lowest-energy frame near ``center``.

    Searches frames within ``[center - radius, center + radius]`` and returns the
    start of the quietest one — the natural pause to begin the next chunk at. If
    the search region is too short to hold a frame (e.g. dense audio at the tail),
    falls back to ``center`` so the loop keeps making forward progress.
    """
    lo = max(0, center - radius)
    hi = min(audio.shape[0], center + radius)
    region = audio[lo:hi]
    n_frames = region.shape[0] // frame
    if n_frames < 1:
        return center
    frames = region[: n_frames * frame].reshape(n_frames, frame)
    energy = np.mean(frames.astype(np.float32) ** 2, axis=1)
    return lo + int(np.argmin(energy)) * frame


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    chunk_len = _CHUNK_SECONDS * sr
    if chunk_len <= 0 or audio.shape[0] == 0:
        return ""

    overlap_len = _OVERLAP_SECONDS * sr
    nominal_stride = max(chunk_len - overlap_len, 1)
    radius = int(_BOUNDARY_SEARCH_SECONDS * sr)
    frame = max(int(_ENERGY_FRAME_SECONDS * sr), 1)
    n = audio.shape[0]

    words: list[str] = []
    start = 0
    while start < n:
        chunk = audio[start : start + chunk_len]

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        # Every 0715 file is deletion-dominated (length_ratio 0.69–0.96, all
        # < 1.0) while hallucination/insertion stays ~0 — the model under-emits
        # on this long-form conversational audio. length_penalty > 1 biases the
        # beam toward longer hypotheses, but it saturated by 1.5 (the 2.0 sweep
        # gave ~0): with the default patience=1, beam search stops the instant
        # ``beam_size`` hypotheses reach EOS, so the shortest finishers fix the
        # output before the length bias can prefer a longer one. patience>1 keeps
        # the search alive until ``beam_size * patience`` hypotheses finish,
        # giving length_penalty the longer candidates it needs to actually act
        # on — unlocking further deletion reduction without changing the chunk
        # count (and thus the runtime budget). Held at 1.5 rather than 2.0 to
        # keep the extra beam steps modest.
        results = generate(
            features,
            [prompt_tokens],
            beam_size=5,
            length_penalty=1.5,
            patience=1.5,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text = processor.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
        if text:
            new_words = text.split()
            if words:
                new_words = _drop_overlap(words, new_words)
            words.extend(new_words)

        if start + chunk_len >= n:
            break
        # Advance by the nominal stride, then snap the next start back/forward to
        # the quietest nearby frame so the chunk begins in a pause. The minimum
        # possible next start is ``nominal_stride - radius`` ahead, so progress is
        # always strictly forward and the loop terminates.
        start = _quiet_boundary(audio, start + nominal_stride, radius, frame)

    return " ".join(words)
