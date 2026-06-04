"""Workspace transcribe — autoresearch evolves this file.

Energy-VAD segmentation pipeline. The previous pipeline sliced the waveform
into *blind* fixed 30s windows: boundaries fell mid-word (losing tokens at
every seam) and any window that happened to contain a long silence (the 0715
recordings carry 6–25% silence, with single gaps up to 34s) fed that silence
straight to the decoder, which is exactly what triggers Whisper's
repetition/hallucination collapse — and a collapsed window deletes its real
content, so this shows up as the dominant deletion axis (del 80%,
length_ratio 0.65, repeated_text 0.36).

Here the raw waveform's own frame energy is used to find speech regions, merge
them across short gaps, and pack them into <=30s windows that break ONLY at
silence. Clear inter-speech silence is never fed to the model; window seams
land in silence instead of mid-word. Peak GPU memory is still one window
(per-window generate), so the long-recording OOM stays fixed.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30
_FRAME_SECONDS = 0.02
_MIN_SILENCE_SECONDS = 0.5
_PAD_SECONDS = 0.25
_SILENCE_DB_BELOW_PEAK = 40.0


def _speech_windows(audio: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Sample-index windows (<=30s) bounded by silence, via frame-energy VAD."""
    n = audio.shape[0]
    chunk_len = int(_CHUNK_SECONDS * sr)
    frame = max(int(_FRAME_SECONDS * sr), 1)
    n_frames = n // frame
    if n_frames == 0:
        return [(0, n)]

    frames = audio[: n_frames * frame].reshape(n_frames, frame).astype(np.float64)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    db = 20.0 * np.log10(rms + 1e-12)
    speech = db > (db.max() - _SILENCE_DB_BELOW_PEAK)

    idx = np.flatnonzero(speech)
    if idx.size == 0:
        return [(0, n)]

    # Merge speech frames into regions, bridging silences shorter than the gap.
    gap_frames = max(int(np.ceil(_MIN_SILENCE_SECONDS / _FRAME_SECONDS)), 1)
    pad = int(_PAD_SECONDS * sr)
    regions: list[tuple[int, int]] = []
    seg_start = prev = idx[0]
    for i in idx[1:]:
        if i - prev > gap_frames:
            regions.append((seg_start, prev + 1))
            seg_start = i
        prev = i
    regions.append((seg_start, prev + 1))

    # Pack regions into <=30s windows, force-splitting any single long region.
    windows: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_end = 0
    for fs, fe in regions:
        s = max(0, fs * frame - pad)
        e = min(n, fe * frame + pad)
        while s < e:
            piece_end = min(e, s + chunk_len)
            if cur_start is None:
                cur_start, cur_end = s, piece_end
            elif piece_end - cur_start <= chunk_len:
                cur_end = piece_end
            else:
                windows.append((cur_start, cur_end))
                cur_start, cur_end = s, piece_end
            s = piece_end
    if cur_start is not None:
        windows.append((cur_start, cur_end))
    return windows


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    audio = np.asarray(audio).reshape(-1)

    texts = []
    for start, end in _speech_windows(audio, sr):
        chunk = audio[start:end]
        if chunk.shape[0] == 0:
            continue
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        text = processor.tokenizer.decode(token_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text.strip())

    return " ".join(texts)
