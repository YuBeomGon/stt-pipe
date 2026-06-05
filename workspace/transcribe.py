"""Workspace transcribe — autoresearch evolves this file.

Context-conditioned decoding. Coverage is no longer the binding constraint:
the silence-aligned segmentation already lifted length_ratio to ~0.93 with the
deletion axis collapsed, and the error profile flipped — substitution now
dominates (53% sub >> del/ins). That axis is not about *whether* text is
emitted but *what* token gets recognized: domain vocabulary (insurance /
call-center jargon, proper nouns) and phone-band-degraded words that Whisper
resolves to a plausible-but-wrong neighbour.

Every prior pipeline decoded each chunk **stateless** — the `prompts` argument
to generate() was always the same fixed `[SOT, lang, task, notimestamps]`
prefix, so each generate() call started cold with zero lexical context. That
throws away Whisper's context channel. This rewrite makes decoding *stateful*:
each chunk is primed with the tail of the running transcript, injected through
the `<|startofprev|>` token convention that Whisper's decoder uses for
"condition on previous text". Priming biases the decoder toward vocabulary it
has already committed to in this very call — the same speaker, the same domain
terms — which is the structural lever over substitution, not coverage.

Context is *sliding* (only the previous chunk's text, capped in tokens), not
accumulated, so a single mis-decode cannot propagate unboundedly down the file.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

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
# Whisper's decoder context is 448 tokens; reserve roughly half for the prompt
# so the generated continuation still has room. Cap the carried-over context
# well under that so priming can never starve generation.
_MAX_CONTEXT_TOKENS = 180


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

        results = generate(
            features,
            [prompt],
            beam_size=1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text = tokenizer.decode(token_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text.strip())
            # Carry this chunk's text (tail only) as context for the next one.
            ctx = tokenizer.encode(text.strip(), add_special_tokens=False)
            prev_context_ids = ctx[-_MAX_CONTEXT_TOKENS:]

    return " ".join(texts)
