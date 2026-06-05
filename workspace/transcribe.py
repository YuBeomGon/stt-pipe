"""Workspace transcribe — autoresearch evolves this file.

Context-conditioned decoding. The dominant error axis is *substitution*
(55%, with length_ratio already ~0.95) — coverage is fine, but domain terms
and phone-band words get mis-recognized. No segmentation/timestamp change can
fix *what* word the decoder picks; that is set by the decoder's language prior.

Whisper exposes a native channel for steering that prior that the prior
pipelines never touched: the *prompt grammar*. Every earlier iteration passed
only the bare ``[<|startoftranscript|>, <|ko|>, <|transcribe|>]`` SOT
sequence. But the decoder also accepts a ``<|startofprev|>`` prefix carrying
arbitrary previous-text tokens (Whisper's `condition_on_previous_text` /
`initial_prompt` mechanism). We use it for a single, *static* purpose:

  - a **domain glossary** (insurance call-center vocabulary) so
    substitution-prone terms are biased toward the correct spelling.

The parent additionally fed the **rolling tail** of its own transcript back
into the prompt each window. That regressed the dominant axis (substitution
0.55 → 0.64): a mis-recognised token in one window re-primes the same wrong
spelling in the next — Whisper's documented `condition_on_previous_text`
error-propagation failure (HF leaves it off by default for this reason). We
drop the self-feedback and keep the prompt context *fixed* to the glossary, so
it biases toward in-domain terms without compounding the decoder's own errors.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_PREV_TOKEN = "<|startofprev|>"

_WINDOW_SECONDS = 30.0
_TIME_PRECISION = 0.02
_MIN_ADVANCE_SECONDS = 2.0

# The decoder reserves ~224 positions for the previous-text prompt. Stay well
# under so the glossary never crowds out the audio decode.
_MAX_PREV_TOKENS = 200

# Insurance call-center domain glossary. These are the terms the phone-band
# audio most often substitutes away from; priming them via <|startofprev|>
# biases the language prior toward the in-domain spelling.
_GLOSSARY = (
    "보험 계약자 피보험자 보험금 보험료 약관 청구 가입 갱신 해지 만기 특약 담보 "
    "자기부담금 보장 면책 고객님 상담 본인확인 주민등록번호 계좌 납입 환급 청약 "
    "증권 손해사정 실손 입원 통원 진단서 영수증 자동이체 수익자 보상"
)


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()
    tokenizer = processor.tokenizer

    audio = np.asarray(audio, dtype=np.float32)
    n_samples = audio.shape[0]
    win_samples = int(_WINDOW_SECONDS * sr)

    timestamp_begin = tokenizer.convert_tokens_to_ids("<|0.00|>")
    prev_id = tokenizer.convert_tokens_to_ids(_PREV_TOKEN)
    sot_tokens = tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )

    glossary_tokens = tokenizer.encode(_GLOSSARY, add_special_tokens=False)

    # Static prompt context: the domain glossary, fixed for every window. No
    # rolling transcript tail (see module docstring — it propagated substitution
    # errors across windows).
    prev_context = glossary_tokens[-_MAX_PREV_TOKENS:]
    prompt_tokens = [prev_id, *prev_context, *sot_tokens]

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

        res = generate(
            features,
            [prompt_tokens],
            beam_size=5,
            sampling_temperature=0.0,
        )[0]
        token_ids = res.sequences_ids[0]

        text_tokens = [t for t in token_ids if t < timestamp_begin]
        ts_tokens = [t for t in token_ids if t >= timestamp_begin]

        text = tokenizer.decode(text_tokens, skip_special_tokens=True).strip()
        if text:
            pieces.append(text)

        advance_seconds = _WINDOW_SECONDS
        if ts_tokens:
            last_ts = (ts_tokens[-1] - timestamp_begin) * _TIME_PRECISION
            if last_ts >= _MIN_ADVANCE_SECONDS:
                advance_seconds = min(last_ts, chunk_seconds)

        cursor += max(1, int(advance_seconds * sr))

    return " ".join(pieces)
