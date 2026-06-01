"""
harness/signature.py
Harness-derived family/signature for candidate diffs (proposal §5).

candidate 가 자기보고하는 `family_id`/`fingerprint` 는 토큰만 바꾸면 우회되므로
다양성 측정(목표 1)과 cooldown 의 정본으로 쓰지 않는다. 대신 diff 의 구조적
feature 를 추출해 결정적으로 signature/family 를 만든다.

- `compute_signature` : 거의 같은 변경인지 보는 세부 지문 (exact-repeat/hard cooldown)
- `assign_family`     : 비슷한 계열을 묶는 상위 그룹 (diversity, family_best)

전부 순수함수 — runner 상태를 건드리지 않는다. hash 는 안정적이어야 하므로
`Math.random`/시각 의존 없이 정렬된 feature tuple 만으로 만든다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# Diff 추가줄에서 찾는 backend/API/알고리즘 keyword (substring, 소문자 비교).
# 새 표면이 나오면 여기 추가한다. 카테고리는 STAGE_MAP 으로 stage 에 매핑.
_API_VOCAB: tuple[str, ...] = (
    # decoding
    "generate", "beam_size", "patience", "length_penalty", "repetition_penalty",
    "no_repeat_ngram", "temperature", "sampling", "num_hypotheses", "return_scores",
    "best_of", "compression",
    # prompt / longform
    "startofprev", "startoftranscript", "detect_language", "language", "notimestamps",
    "prompt", "seek", "timestamp", "chunk", "window", "stride", "segment",
    # confidence
    "no_speech", "logprob", "fallback", "threshold",
    # suppress
    "suppress", "blank", "mask",
    # alignment / rerank
    "align", "forced", "rerank", "rover", "consensus", "mbr", "nbest", "n_best",
    # audio frontend
    "preemphasis", "channel_eq", "compand", "cmn", "cmvn", "vtln", "log_mel",
    "gain", "highpass", "lowpass",
    # vad
    "vad", "silence", "silero",
    # postprocess
    "regex", "punctuation", "spacing", "normalize",
)

# keyword substring → pipeline stage token. 첫 매칭 stage 를 부여(다중 가능).
_STAGE_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("audio_frontend", ("preemphasis", "channel_eq", "compand", "cmn", "cmvn",
                         "vtln", "log_mel", "gain", "highpass", "lowpass")),
    ("segmentation", ("seek", "chunk", "window", "stride", "segment", "vad",
                      "silence", "silero")),
    ("decoding", ("beam", "patience", "temperature", "penalty", "ngram",
                  "sampling", "generate", "num_hypotheses", "best_of")),
    ("confidence_gate", ("no_speech", "logprob", "compression", "fallback",
                         "threshold")),
    ("rerank", ("rerank", "rover", "consensus", "mbr", "nbest", "n_best")),
    ("suppress", ("suppress", "blank", "mask")),
    ("prompt", ("prompt", "startofprev", "language", "timestamp", "notimestamps")),
    ("postprocess", ("regex", "punctuation", "spacing", "normalize")),
)

# 추가줄에서 hunk 안 함수 region. `@@ ... @@ def NAME(` 또는 추가된 `def NAME`.
_HUNK_REGION_RE = re.compile(r"^@@.*@@\s*(?:async\s+)?def\s+([A-Za-z_]\w*)")
_ADDED_DEF_RE = re.compile(r"^\+\s*(?:async\s+)?def\s+([A-Za-z_]\w*)")
# 튜닝 param: 숫자 리터럴을 받는 kwarg/지역명 (beam_size=5, patience=2.0, threshold=2.4).
# 지역 변수 rename (mask = build()...) 처럼 비숫자 RHS 는 잡지 않아 rename 노이즈를 줄임.
_NUMERIC_PARAM_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*=\s*-?\d")
# UPPERCASE 상수 정의.
_CONST_RE = re.compile(r"\b([A-Z_]{2,})\s*=")

# Jaccard ≥ 이 값이면 같은 family. 보수적(낮은 merge) — false-split > false-merge
# (proposal §5.3: 서로 다른 계열을 합치면 diversity 평가가 망가진다).
DEFAULT_FAMILY_THRESHOLD: float = 0.5


@dataclass(frozen=True)
class Features:
    touched_regions: frozenset[str] = field(default_factory=frozenset)
    api_keywords: frozenset[str] = field(default_factory=frozenset)
    changed_params: frozenset[str] = field(default_factory=frozenset)
    stage_tokens: frozenset[str] = field(default_factory=frozenset)


def feature_tokens(f: Features) -> frozenset[str]:
    """모든 feature 를 카테고리 prefix 로 평탄화한 토큰 집합 (Jaccard/hash 입력)."""
    return frozenset(
        [f"region:{x}" for x in f.touched_regions]
        + [f"api:{x}" for x in f.api_keywords]
        + [f"param:{x}" for x in f.changed_params]
        + [f"stage:{x}" for x in f.stage_tokens]
    )


def _added_text(diff_text: str) -> str:
    """diff 의 추가줄(+)만 모은 본문 (헤더 +++ 제외)."""
    lines = []
    for line in diff_text.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
    return "\n".join(lines)


def extract_features(diff_text: str, meta: dict | None = None) -> Features:
    """unified diff 텍스트에서 구조적 feature 를 추출한다. meta 는 현재 미사용
    (self-declared 값은 신뢰 기준이 아니므로 grouping 에 넣지 않는다)."""
    regions: set[str] = set()
    for line in diff_text.splitlines():
        m = _HUNK_REGION_RE.match(line)
        if m:
            regions.add(m.group(1))
        m = _ADDED_DEF_RE.match(line)
        if m:
            regions.add(m.group(1))

    added = _added_text(diff_text)
    added_low = added.lower()

    api = {kw for kw in _API_VOCAB if kw in added_low}

    params: set[str] = set()
    for line in added.splitlines():
        params.update(_NUMERIC_PARAM_RE.findall(line))
        params.update(_CONST_RE.findall(line))

    stages: set[str] = set()
    for stage, kws in _STAGE_MAP:
        if any(kw in added_low for kw in kws):
            stages.add(stage)

    return Features(
        touched_regions=frozenset(regions),
        api_keywords=frozenset(api),
        changed_params=frozenset(params),
        stage_tokens=frozenset(stages),
    )


def compute_signature(features: Features) -> str:
    """feature 토큰을 정렬해 안정적 hash → `sig_xxxxxxxx`. 같은 feature → 같은 sig."""
    tokens = tuple(sorted(feature_tokens(features)))
    digest = hashlib.sha1(repr(tokens).encode("utf-8")).hexdigest()[:8]
    return f"sig_{digest}"


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    if not union:
        return 0.0  # 빈 feature 끼리는 자동 merge 하지 않는다 (보수적).
    return len(a & b) / len(union)


def assign_family(
    features: Features,
    known: dict[str, Features],
    threshold: float = DEFAULT_FAMILY_THRESHOLD,
) -> tuple[str, bool]:
    """features 를 기존 family 중 가장 가까운 것에 배정하거나 새 family 를 만든다.

    `known` 은 {family_id: 대표 Features}. 반환 (family_id, is_new). 새 id 는
    `family_{N+1:03d}` (결정적). 동률은 정렬된 family_id 중 첫 번째를 택해 재현성 유지.
    """
    tokens = feature_tokens(features)
    best_fid: str | None = None
    best_j = 0.0
    for fid in sorted(known):
        j = jaccard(tokens, feature_tokens(known[fid]))
        if j > best_j:
            best_j = j
            best_fid = fid
    if best_fid is not None and best_j >= threshold:
        return best_fid, False
    return f"family_{len(known) + 1:03d}", True
