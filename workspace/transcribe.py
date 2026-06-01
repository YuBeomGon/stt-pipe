"""Workspace transcribe — autoresearch evolves this file.

Long-form windowing (sequential): Whisper's feature extractor pads/truncates
every input to a single 30s window, so the stub dropped everything past the
first 30s of a multi-minute 0715 call (massive deletion regime). We split the
waveform into consecutive 30s windows and decode them ONE AT A TIME, then
concatenate the per-window text in order. The earlier batched variant fed all
windows into a single ``generate()`` call and OOM'd the GPU on long calls;
sequential decoding caps peak memory at one 30s window.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_WINDOW_SECONDS = 30


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    audio = np.asarray(audio).reshape(-1)
    window = int(_WINDOW_SECONDS * sr)
    n_windows = max(1, int(np.ceil(len(audio) / window)))

    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )

    texts = []
    for i in range(n_windows):
        chunk = audio[i * window : (i + 1) * window]
        if len(chunk) == 0:
            continue
        inputs = processor(
            chunk,
            sampling_rate=sr,
            return_tensors="np",
        )
        # input_features is (1, n_mels, n_frames); the extractor pads the final
        # short chunk up to the full 30s window for us.
        features = to_storage_view(inputs.input_features)

        results = generate(
            features,
            [prompt_tokens],
            beam_size=1,
            sampling_temperature=0.0,
        )

        token_ids = results[0].sequences_ids[0]
        texts.append(processor.tokenizer.decode(token_ids, skip_special_tokens=True))

    return " ".join(t.strip() for t in texts if t.strip())
