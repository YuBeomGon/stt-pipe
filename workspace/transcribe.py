"""Workspace transcribe — autoresearch evolves this file.

Initial stub: one 30-second window through the frozen Whisper backend.
Long-form audio (most of 0715) will get its tail truncated; that is the
starting point autoresearch is supposed to improve.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import re

import librosa
import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_PREV_MAX_TOKENS = 300
_INITIAL_PROMPT_TEXT = (
    "보험 약관 청구 가입 보장 동의 고객 어머니 선생님 계약 갱신 면책 담보 특약 "
    "피보험자 본인 부담금 실손 의료비 통원 입원 수술 상해 질병 사망 후유 장애 "
    "고지 의무 청약 해지 환급 만기 보험료 보험금"
)


def _vad_chunks(audio: np.ndarray, sr: int, max_seconds: int = 30) -> list[tuple[int, int]]:
    """librosa.effects.split (top_db=25) → max_seconds 까지 합친 (start, end) 리스트.
    양쪽 0.2s margin 확장 (단어 경계 절단 보호)."""
    max_samples = max_seconds * sr
    margin_samples = int(0.2 * sr)
    intervals = librosa.effects.split(audio, top_db=28)
    if len(intervals) == 0:
        return [(0, len(audio))]

    chunks: list[tuple[int, int]] = []
    cur_start, cur_end = int(intervals[0][0]), int(intervals[0][1])
    for s, e in intervals[1:]:
        s_i, e_i = int(s), int(e)
        if e_i - cur_start <= max_samples:
            cur_end = e_i
        else:
            chunks.append((cur_start, cur_end))
            cur_start, cur_end = s_i, e_i
    chunks.append((cur_start, cur_end))

    audio_len = len(audio)
    return [
        (max(0, s - margin_samples), min(audio_len, e + margin_samples))
        for s, e in chunks
    ]


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    chunks = _vad_chunks(audio, sr, max_seconds=25)

    sot_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN, "<|notimestamps|>"]
    )
    startofprev_id = processor.tokenizer.convert_tokens_to_ids("<|startofprev|>")
    initial_prompt_tokens = processor.tokenizer.encode(
        _INITIAL_PROMPT_TEXT, add_special_tokens=False
    )

    prev_tokens: list[int] = []
    texts = []
    for start, end in chunks:
        chunk = audio[start:end]
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        if prev_tokens:
            prompt = [startofprev_id] + prev_tokens[-_PREV_MAX_TOKENS:] + sot_tokens
        else:
            prompt = [startofprev_id] + initial_prompt_tokens + sot_tokens

        results = generate(
            features,
            [prompt],
            beam_size=8,
            length_penalty=2.0,
            patience=2.5,
            no_repeat_ngram_size=7,
            sampling_temperature=0.0,
        )
        out_tokens = list(results[0].sequences_ids[0])
        texts.append(processor.tokenizer.decode(out_tokens, skip_special_tokens=True))
        prev_tokens = out_tokens

    merged = " ".join(t.strip() for t in texts if t.strip())
    return re.sub(r"\s+", " ", merged).strip()
