"""Workspace transcribe — autoresearch evolves this file.

Confidence-gated escalation decoding. Every prior pipeline called generate()
exactly once per chunk and read only ``results[0].sequences_ids[0]`` — the
rest of the return channel was discarded. CTranslate2's Whisper.generate can
also return ``.scores`` (length-normalized log-likelihood per hypothesis, when
``return_scores=True``) and ``.no_speech_prob``. Those fields are an unused
backend capability: a *per-chunk confidence signal* the harness never exploited.

The two focus files are flagged for ``repeated_text`` — decoder collapse into
repetition on hard spans, which a single greedy pass cannot recover from. This
rewrite turns each chunk's decode into a two-pass loop. Pass A is the cheap
greedy decode we already used; it is now *gated* by two cheap signals — the
returned ``.scores`` (avg log-prob) and the zlib compression ratio of the
decoded text (high ratio == repetition). Only chunks that fail the gate spend a
second, more expensive beam-search pass with explicit anti-repetition
(``no_repeat_ngram_size`` + ``repetition_penalty``), then we keep whichever
candidate repeats less. The common case stays at the current greedy cost; only
collapsed chunks pay extra, so runtime tracks the incumbent.

Segmentation (silence-aligned chunks) and sliding ``<|startofprev|>`` priming
are retained — they fixed coverage and are orthogonal to the decode strategy.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import zlib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Stay under Whisper's hard 30s mel window, with headroom so a silence-aligned
# chunk never overflows the forward pass.
_MAX_CHUNK_SECONDS = 28.0
# Energy VAD frame size.
_FRAME_MS = 30.0
# Minimum silence-run length that qualifies as a safe cut point.
_MIN_SILENCE_S = 0.35
# Whisper's decoder context is 448 tokens; cap carried-over context well under
# that so priming can never starve generation.
_MAX_CONTEXT_TOKENS = 180

# Gate thresholds (Whisper reference defaults). A chunk whose greedy decode has
# avg log-prob below _LOGPROB_THRESH OR a compression ratio above _CR_THRESH is
# treated as a collapse/low-confidence decode and re-decoded with beam search.
_LOGPROB_THRESH = -1.0
_CR_THRESH = 2.4


def _silence_aligned_bounds(audio: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Partition [0, len(audio)) into chunks <= max, cutting at silence centres.

    Energy per ``_FRAME_MS`` frame is compared to a low percentile of the
    file's own energy distribution to mark silent frames; silence runs longer
    than ``_MIN_SILENCE_S`` become candidate cut points. We then greedily take
    the farthest candidate within one max-chunk of the current start, falling
    back to a hard cut at the max length only when no silence is in reach.
    """
    max_len = int(_MAX_CHUNK_SECONDS * sr)
    frame = int(sr * _FRAME_MS / 1000.0)
    if frame <= 0 or len(audio) <= max_len:
        return [(0, len(audio))]

    n = len(audio) // frame
    frames = audio[: n * frame].reshape(n, frame).astype(np.float64)
    energy = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    # Threshold between the file's noise floor and its speech energy. The 30th
    # percentile sits inside silence for typical call-center duty cycles.
    thr = np.percentile(energy, 30)
    silent = energy <= thr

    min_sil_frames = max(1, int(_MIN_SILENCE_S / (_FRAME_MS / 1000.0)))
    cuts: list[int] = []
    i = 0
    while i < n:
        if silent[i]:
            j = i
            while j < n and silent[j]:
                j += 1
            if j - i >= min_sil_frames:
                cuts.append(((i + j) // 2) * frame)
            i = j
        else:
            i += 1

    bounds: list[tuple[int, int]] = []
    start = 0
    total = len(audio)
    while start < total:
        limit = start + max_len
        if limit >= total:
            bounds.append((start, total))
            break
        reachable = [c for c in cuts if start < c <= limit]
        end = max(reachable) if reachable else limit
        bounds.append((start, end))
        start = end
    return bounds


def _compression_ratio(text: str) -> float:
    """Repetition proxy: original bytes / zlib-compressed bytes.

    Highly repetitive text compresses far better, so a high ratio is a direct
    signal of the repeated-text collapse the focus files are flagged for.
    """
    data = text.encode("utf-8")
    if not data:
        return 0.0
    return len(data) / len(zlib.compress(data))


def _decode_chunk(features, prompt, tokenizer) -> str:
    """Decode one chunk, escalating to beam search only on a bad greedy pass.

    Reads the previously-discarded ``.scores`` return channel: Pass A is the
    cheap greedy decode, accepted iff its avg log-prob and compression ratio
    clear the gates. A failing chunk gets Pass B — beam search with explicit
    anti-repetition — and we keep whichever candidate repeats less.
    """
    res_a = generate(
        features,
        [prompt],
        beam_size=1,
        sampling_temperature=0.0,
        return_scores=True,
    )
    ids_a = res_a[0].sequences_ids[0]
    score_a = res_a[0].scores[0]
    text_a = tokenizer.decode(ids_a, skip_special_tokens=True).strip()
    cr_a = _compression_ratio(text_a)

    if score_a >= _LOGPROB_THRESH and cr_a <= _CR_THRESH:
        return text_a

    res_b = generate(
        features,
        [prompt],
        beam_size=5,
        num_hypotheses=1,
        length_penalty=1.0,
        repetition_penalty=1.1,
        no_repeat_ngram_size=3,
        return_scores=True,
    )
    ids_b = res_b[0].sequences_ids[0]
    score_b = res_b[0].scores[0]
    text_b = tokenizer.decode(ids_b, skip_special_tokens=True).strip()
    cr_b = _compression_ratio(text_b)

    # Prefer the candidate that repeats less; tie-break on higher confidence.
    rank_a = (cr_a, -score_a)
    rank_b = (cr_b, -score_b)
    return text_a if rank_a <= rank_b else text_b


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    sot_prefix = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )
    startofprev = tokenizer.convert_tokens_to_ids("<|startofprev|>")

    # A chunk shorter than this is sub-100ms of real signal — only silence
    # hallucination lives there.
    min_samples = sr // 10

    texts: list[str] = []
    # Token ids of the previous chunk's transcript, used to prime the next
    # decode through the <|startofprev|> context channel. Sliding (last chunk
    # only), capped, so a single bad decode cannot propagate down the file.
    prev_context_ids: list[int] = []

    for start, end in _silence_aligned_bounds(audio, sr):
        chunk = audio[start:end]
        if len(chunk) < min_samples:
            continue

        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        features = to_storage_view(inputs.input_features)

        if prev_context_ids:
            prompt = [startofprev] + prev_context_ids + sot_prefix
        else:
            prompt = list(sot_prefix)

        text = _decode_chunk(features, prompt, tokenizer)
        if text:
            texts.append(text)
            # Carry this chunk's text (tail only) as context for the next one.
            ctx = tokenizer.encode(text, add_special_tokens=False)
            prev_context_ids = ctx[-_MAX_CONTEXT_TOKENS:]

    return " ".join(texts)
