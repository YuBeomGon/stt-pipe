"""Workspace transcribe — autoresearch evolves this file.

Context-conditioned decoding (family_003) with a beam-search tune. The parent
established the structural pipeline: silence-aligned segmentation tiles the
whole recording into self-contained, ≤28s spans cut at the centre of a silence
run (so no generate() call sees a mid-utterance fragment), and each chunk is
decoded **statefully** — primed with the tail of the running transcript through
the ``<|startofprev|>`` token convention so the decoder is biased toward
vocabulary it has already committed to (same speaker, same insurance /
call-center domain terms). Coverage is healthy (length_ratio ≈ 0.95); the
binding error is now **substitution** (59% ≫ del/ins): the right span is heard
but a plausible-but-wrong neighbour token is emitted.

REFINE: the parent decoded greedily (beam_size=1). Greedy commits to the
locally-best token at each step, which is exactly how a degraded phone-band or
domain word collapses to a near-homophone. This slot flips the decode to beam
search (beam_size=5) so multiple hypotheses survive and the length-normalized
score can prefer the globally-coherent transcript — the standard, direct lever
over substitution, not coverage. Everything else (segmentation, sliding
priming context capped at _MAX_CONTEXT_TOKENS) is unchanged from the parent.
The parent's greedy pass cost ~152s of inference; beam=5 stays well inside the
~720s budget.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"

# Whisper's hard mel window is 30s; keep chunks under it so a silence-aligned
# tail extension can never overflow the forward pass.
_MAX_CHUNK_SECONDS = 28.0
# Frame granularity of the energy VAD.
_FRAME_MS = 30.0
# Minimum silence-run length that qualifies as a safe cut point.
_MIN_SILENCE_S = 0.35
# Whisper's decoder context is 448 tokens; cap carried-over priming well under
# half so it can never starve generation of the new chunk.
_MAX_CONTEXT_TOKENS = 180
# Beam width — >1 lets alternative hypotheses survive so the length-normalized
# score can override a greedy substitution.
_BEAM_SIZE = 5
# Length penalty exponent for beam finalization. CT2 normalizes a beam's score
# by length**_LENGTH_PENALTY; the default (1.0) is exact per-token averaging,
# which on this audio let beam=5 prefer a clipped hypothesis (del 0.35→0.37 in
# iter_007). A value >1 over-normalizes so longer, more-complete hypotheses are
# no longer out-scored by short ones — recovering the coverage beam shed while
# keeping the substitution reduction beam buys.
_LENGTH_PENALTY = 1.3


def _silence_aligned_bounds(audio: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Partition [0, len(audio)) into chunks ≤ _MAX_CHUNK_SECONDS whose
    boundaries fall in the middle of a silence run whenever one is reachable
    within the window; otherwise cut at the hard window edge. Coverage is total
    — the chunks tile the signal exactly."""
    total = len(audio)
    max_len = int(_MAX_CHUNK_SECONDS * sr)
    frame = max(1, int(_FRAME_MS / 1000.0 * sr))
    n = total // frame
    if n == 0:
        return [(0, total)]

    trimmed = audio[: n * frame].astype(np.float64).reshape(n, frame)
    energy = np.sqrt(np.mean(trimmed * trimmed, axis=1))
    # Percentile-anchored silence threshold: robust to overall gain, marks the
    # quietest ~20% of frames (scaled) as candidate silence.
    thresh = float(np.percentile(energy, 20)) * 1.5
    is_sil = energy < thresh

    min_sil_frames = max(1, int(_MIN_SILENCE_S * 1000.0 / _FRAME_MS))
    sil_centers: list[int] = []
    i = 0
    while i < n:
        if is_sil[i]:
            j = i
            while j < n and is_sil[j]:
                j += 1
            if (j - i) >= min_sil_frames:
                sil_centers.append(((i + j) // 2) * frame)
            i = j
        else:
            i += 1
    centers = np.asarray(sil_centers, dtype=np.int64)

    bounds: list[tuple[int, int]] = []
    start = 0
    while start < total:
        target_end = min(start + max_len, total)
        if target_end >= total:
            bounds.append((start, total))
            break
        if centers.size:
            cands = centers[(centers > start) & (centers <= target_end)]
            end = int(cands[-1]) if cands.size else target_end
        else:
            end = target_end
        if end <= start:
            end = target_end
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

    # Sub-100ms of signal is silence residue, not worth a forward pass.
    min_samples = sr // 10

    texts: list[str] = []
    prev_context_ids: list[int] = []

    for start, end in _silence_aligned_bounds(audio, sr):
        chunk = audio[start:end]
        if len(chunk) < min_samples:
            continue

        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        if prev_context_ids:
            prompt = [startofprev] + prev_context_ids + list(sot_prefix)
        else:
            prompt = list(sot_prefix)

        res = generate(
            features,
            [prompt],
            beam_size=_BEAM_SIZE,
            length_penalty=_LENGTH_PENALTY,
            sampling_temperature=0.0,
        )
        ids = res[0].sequences_ids[0]
        text = tokenizer.decode(ids, skip_special_tokens=True).strip()
        if text:
            texts.append(text)
            # Sliding (not accumulated) context: only the most recent text,
            # capped, so a single mis-decode cannot propagate down the file.
            ctx = tokenizer.encode(text, add_special_tokens=False)
            prev_context_ids = ctx[-_MAX_CONTEXT_TOKENS:]

    return " ".join(texts)
