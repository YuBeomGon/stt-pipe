"""Workspace transcribe — autoresearch evolves this file.

Temperature-fallback long-form decoding — the OpenAI Whisper reference
robustness loop, which this job has never actually run. Every prior pipeline
decoded each 30s window exactly ONCE, greedily (``beam_size=1``,
``sampling_temperature=0.0``), and committed to whatever token path that single
pass produced. So any window where greedy locked onto a wrong path stayed
wrong — which is exactly the substitution-dominant error profile (sub 57% with
length_ratio 0.94 healthy: the model emits roughly the right length but the
wrong characters, the signature of a bad greedy commit that nothing re-examines).

This pipeline decodes each window against an escalating temperature ladder
behind a quality gate built from the backend's return channels: ``return_scores``
(iter_006's discovery) gives the hypothesis avg log-prob, and a zlib
compression ratio over the decoded text catches repetition collapse. A window
is accepted at the *first* temperature whose decode clears both the avg-logprob
and compression-ratio thresholds; a window that keeps failing — a
low-confidence substitution path, or a repetition loop (high compression
ratio) — is re-decoded at a higher temperature (sampling from the full
distribution), and if nothing clears the gate the highest-logprob attempt is
kept. This is the standard mechanism for escaping a bad greedy commit and is
the one robustness loop the pipeline never had; clean windows still pass at
t=0.0 on the first decode, so the extra compute lands only on the windows that
need it.

Contract (`STT-PIPELINE-SPEC.md §10`): ``transcribe(audio, sr) -> str``.
"""

from __future__ import annotations

import zlib

import numpy as np

from frozen.asr_backend import generate, load, to_storage_view

_LANGUAGE_TOKEN = "<|ko|>"
_TASK_TOKEN = "<|transcribe|>"
_CHUNK_SECONDS = 30.0
_TIME_PRECISION = 0.02  # Whisper timestamp token resolution, seconds per step.
_MIN_ADVANCE_SECONDS = 1.0  # below this the last-timestamp boundary is untrustworthy.

# OpenAI Whisper reference fallback ladder + acceptance gates. A window's decode
# is accepted at the first temperature clearing BOTH gates; failures escalate.
_TEMPERATURES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
_LOGPROB_THRESHOLD = -1.0
_COMPRESSION_RATIO_THRESHOLD = 2.4
# Deterministic first pass uses real beam search (selects by GLOBAL cumulative
# log-prob) instead of greedy — the direct lever for substitution-from-bad-
# local-commit, the dominant axis. Sampling fallbacks stay beam_size=1.
_BEAM_SIZE = 5
_PATIENCE = 1.0

# Fixed correctly-spelled domain glossary (iter_012 static-prior idea, error-
# propagation-free by construction). Placed behind <|startofprev|> to bias the
# decoder LM toward the canonical call-center-insurance lexicon on the
# substitution-risk windows. iter_038 tried this as a BATCHED second prompt and
# crashed (batched generate requires <|startoftranscript|> at the same position
# in every batch entry, which a prev-prefix shifts); here it is a SEPARATE
# single-prompt generate() call, so that constraint never applies.
_GLOSSARY = (
    "보험 보험료 보험금 계약 보장 가입 약관 청약 해지 갱신 특약 납입 만기 "
    "수익자 피보험자 보험사 상담사 고객님 본인 확인 동의 안내 상품 가입자"
)


def _compression_ratio(text: str) -> float:
    """zlib compression ratio of the decoded text — high => repetition loop."""
    data = text.encode("utf-8")
    if not data:
        return 0.0
    return len(data) / len(zlib.compress(data))


def transcribe(audio: np.ndarray, sr: int) -> str:
    model, processor = load()

    # Timestamps ON (omit <|notimestamps|>): the interleaved <|t|> tokens are
    # the long-form seek channel, and survive sampled decodes too.
    prompt_tokens = processor.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", _LANGUAGE_TOKEN, _TASK_TOKEN]
    )
    timestamp_begin = processor.tokenizer.convert_tokens_to_ids("<|0.00|>")

    # Glossary-primed prompt: <|startofprev|> + fixed glossary ids + SOT seq.
    # A separate single-prompt decode (B=1), NOT a batch entry, so it is immune
    # to the same-SOT-position batch constraint that crashed iter_038.
    startofprev_id = processor.tokenizer.convert_tokens_to_ids("<|startofprev|>")
    glossary_ids = processor.tokenizer.encode(_GLOSSARY, add_special_tokens=False)
    glossary_prompt = [startofprev_id] + list(glossary_ids) + list(prompt_tokens)

    audio = np.asarray(audio).reshape(-1)
    n = audio.shape[0]
    chunk_len = int(_CHUNK_SECONDS * sr)
    min_advance = int(_MIN_ADVANCE_SECONDS * sr)

    texts: list[str] = []
    seek = 0
    while seek < n:
        chunk = audio[seek : seek + chunk_len]
        if chunk.shape[0] == 0:
            break
        inputs = processor(chunk, sampling_rate=sr, return_tensors="np")
        features = to_storage_view(inputs.input_features)

        # Temperature-fallback decode: accept the first gate-passing attempt,
        # else fall back to the highest-logprob attempt seen.
        chosen_ids = None
        best_logprob = None
        best_ids = None
        for temp in _TEMPERATURES:
            if temp == 0.0:
                # cond A — bare SOT beam search (the champion's first pass).
                res = generate(
                    features,
                    [prompt_tokens],
                    beam_size=_BEAM_SIZE,
                    patience=_PATIENCE,
                    sampling_temperature=0.0,
                    return_scores=True,
                )[0]
                lp_a = float(res.scores[0])
                text_a = processor.tokenizer.decode(
                    res.sequences_ids[0], skip_special_tokens=True
                )
                cr_a = _compression_ratio(text_a)
                # Only on the substitution-risk windows (cond A doesn't cleanly
                # pass the gate) spend one extra beam pass on the glossary-primed
                # condition, then keep whichever has the higher avg log-prob —
                # the glossary path can only win when its own LM evidence is at
                # least as strong, so it never forces a worse decode. Confident
                # windows skip cond B entirely, keeping runtime near champion.
                if not (lp_a >= _LOGPROB_THRESHOLD and cr_a <= _COMPRESSION_RATIO_THRESHOLD):
                    res_b = generate(
                        features,
                        [glossary_prompt],
                        beam_size=_BEAM_SIZE,
                        patience=_PATIENCE,
                        sampling_temperature=0.0,
                        return_scores=True,
                    )[0]
                    if float(res_b.scores[0]) > lp_a:
                        res = res_b
            else:
                # sampling_topk=0 => sample from the full distribution so the
                # temperature actually perturbs the path (topk=1 would stay greedy).
                res = generate(
                    features,
                    [prompt_tokens],
                    beam_size=1,
                    sampling_topk=0,
                    sampling_temperature=temp,
                    return_scores=True,
                )[0]
            token_ids = res.sequences_ids[0]
            avg_logprob = float(res.scores[0])
            text = processor.tokenizer.decode(token_ids, skip_special_tokens=True)
            cr = _compression_ratio(text)

            if best_logprob is None or avg_logprob > best_logprob:
                best_logprob = avg_logprob
                best_ids = token_ids

            if (
                avg_logprob >= _LOGPROB_THRESHOLD
                and cr <= _COMPRESSION_RATIO_THRESHOLD
            ):
                chosen_ids = token_ids
                break

        token_ids = chosen_ids if chosen_ids is not None else best_ids

        # Long-form seek: advance to the last timestamp the window emitted and
        # emit text up to it; fall back to the full chunk on a degenerate window.
        ts_positions = [i for i, t in enumerate(token_ids) if t >= timestamp_begin]
        advance = chunk_len
        emit_ids = token_ids
        if len(ts_positions) >= 2:
            last_i = ts_positions[-1]
            last_advance = int(
                (token_ids[last_i] - timestamp_begin) * _TIME_PRECISION * sr
            )
            if last_advance >= min_advance:
                advance = last_advance
                emit_ids = token_ids[:last_i]

        text = processor.tokenizer.decode(emit_ids, skip_special_tokens=True)
        if text.strip():
            texts.append(text.strip())

        seek += advance

    return " ".join(texts)
