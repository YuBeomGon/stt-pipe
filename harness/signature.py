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


# 주석/문자열 리터럴 제거용(리뷰 #5): candidate 가 주석/문자열에 키워드를 넣어
# family/signature 를 흔드는 spoofing 차단. diff fragment 라 AST 는 못 쓰고 정규식으로
# 보수적으로 제거(삼중따옴표 → 단일/이중 따옴표 → 인라인 주석 순).
_TRIPLE_STR_RE = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')
_STR_RE = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'')
_COMMENT_RE = re.compile(r"#.*")


def _strip_comments_strings(text: str) -> str:
    text = _TRIPLE_STR_RE.sub(" ", text)
    text = _STR_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    return text


@dataclass(frozen=True)
class Features:
    touched_regions: frozenset[str] = field(default_factory=frozenset)
    api_keywords: frozenset[str] = field(default_factory=frozenset)
    changed_params: frozenset[str] = field(default_factory=frozenset)
    stage_tokens: frozenset[str] = field(default_factory=frozenset)
    # removed(-) 라인의 feature (리뷰 #6): deletion-only / ablate diff 가 region 만
    # 남아 서로 다른 ablation 이 false-merge 되는 것을 막는다. added 와 별도 namespace.
    removed_api: frozenset[str] = field(default_factory=frozenset)
    removed_params: frozenset[str] = field(default_factory=frozenset)
    removed_stages: frozenset[str] = field(default_factory=frozenset)


def feature_tokens(f: Features) -> frozenset[str]:
    """모든 feature 를 카테고리 prefix 로 평탄화한 토큰 집합 (Jaccard/hash 입력)."""
    return frozenset(
        [f"region:{x}" for x in f.touched_regions]
        + [f"api:{x}" for x in f.api_keywords]
        + [f"param:{x}" for x in f.changed_params]
        + [f"stage:{x}" for x in f.stage_tokens]
        + [f"rmapi:{x}" for x in f.removed_api]
        + [f"rmparam:{x}" for x in f.removed_params]
        + [f"rmstage:{x}" for x in f.removed_stages]
    )


def _changed_text(diff_text: str, sign: str) -> str:
    """diff 의 +(추가) 또는 -(삭제) 줄만 모은 본문 (헤더 +++/--- 제외)."""
    header = sign * 3
    lines = []
    for line in diff_text.splitlines():
        if line.startswith(header):
            continue
        if line.startswith(sign):
            lines.append(line[1:])
    return "\n".join(lines)


def _scan(text: str) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """주석/문자열 제거 후 (api_keywords, params, stages) 추출."""
    code = _strip_comments_strings(text)
    low = code.lower()
    api = {kw for kw in _API_VOCAB if kw in low}
    params: set[str] = set()
    for line in code.splitlines():
        params.update(_NUMERIC_PARAM_RE.findall(line))
        params.update(_CONST_RE.findall(line))
    stages = {stage for stage, kws in _STAGE_MAP if any(kw in low for kw in kws)}
    return frozenset(api), frozenset(params), frozenset(stages)


def extract_features(diff_text: str, meta: dict | None = None) -> Features:
    """unified diff 텍스트에서 구조적 feature 를 추출한다. 주석/문자열은 제거하고
    (#5), 추가줄과 삭제줄을 별도 namespace 로 추출한다(#6). meta 는 미사용
    (self-declared 값은 신뢰 기준이 아니므로 grouping 에 넣지 않는다)."""
    regions: set[str] = set()
    for line in diff_text.splitlines():
        m = _HUNK_REGION_RE.match(line)
        if m:
            regions.add(m.group(1))
        m = _ADDED_DEF_RE.match(line)
        if m:
            regions.add(m.group(1))

    api, params, stages = _scan(_changed_text(diff_text, "+"))
    rm_api, rm_params, rm_stages = _scan(_changed_text(diff_text, "-"))

    return Features(
        touched_regions=frozenset(regions),
        api_keywords=api,
        changed_params=params,
        stage_tokens=stages,
        removed_api=rm_api,
        removed_params=rm_params,
        removed_stages=rm_stages,
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


def assign_family_tokens(
    tokens: frozenset[str],
    known_tokens: dict[str, frozenset[str]],
    threshold: float = DEFAULT_FAMILY_THRESHOLD,
) -> tuple[str, bool]:
    """token 집합을 기존 family 의 대표 token 집합과 Jaccard 비교해 배정/신규.

    resume-safe: caller 가 decisions.jsonl 에 저장한 feature_tokens 로
    `known_tokens` 를 재구성해 넘기면, 프로세스가 재시작해도 family 번호가
    일관되게 이어진다. 새 id 는 `family_{N+1:03d}`. 동률은 정렬 첫 family.
    """
    best_fid: str | None = None
    best_j = 0.0
    for fid in sorted(known_tokens):
        j = jaccard(tokens, known_tokens[fid])
        if j > best_j:
            best_j = j
            best_fid = fid
    if best_fid is not None and best_j >= threshold:
        return best_fid, False
    return f"family_{len(known_tokens) + 1:03d}", True


def assign_family(
    features: Features,
    known: dict[str, Features],
    threshold: float = DEFAULT_FAMILY_THRESHOLD,
) -> tuple[str, bool]:
    """features 를 기존 family 중 가장 가까운 것에 배정하거나 새 family 를 만든다.

    `known` 은 {family_id: 대표 Features}. 반환 (family_id, is_new). 새 id 는
    `family_{N+1:03d}` (결정적). 동률은 정렬된 family_id 중 첫 번째를 택해 재현성 유지.
    """
    return assign_family_tokens(
        feature_tokens(features),
        {fid: feature_tokens(kf) for fid, kf in known.items()},
        threshold,
    )
