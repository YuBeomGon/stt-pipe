"""``harness.signature`` — diff 기반 harness-derived family/signature.

Proposal §5. 다양성 측정(목표 1)과 cooldown 의 토대이므로 keystone.
self-declared family 와 무관하게, diff feature 로 결정적·재현적으로 묶는다.
"""

from __future__ import annotations

import pytest

from harness import signature as sig


# ── feature 추출 ─────────────────────────────────────────────────────
def _diff(added: list[str], region: str = "transcribe") -> str:
    """workspace/transcribe.py 에 대한 최소 unified diff 흉내."""
    body = "\n".join(f"+{line}" for line in added)
    return (
        "diff --git a/workspace/transcribe.py b/workspace/transcribe.py\n"
        "--- a/workspace/transcribe.py\n"
        "+++ b/workspace/transcribe.py\n"
        f"@@ -1,3 +1,9 @@ def {region}(audio, sr):\n"
        f"{body}\n"
    )


def test_extract_picks_up_api_keywords() -> None:
    f = sig.extract_features(_diff(["    results = generate(features, beam_size=5)"]))
    tokens = sig.feature_tokens(f)
    assert any("beam_size" in t for t in tokens)


def test_extract_maps_stage_tokens() -> None:
    f = sig.extract_features(
        _diff(["    if compression_ratio > 2.4:", "        # temperature fallback"])
    )
    tokens = sig.feature_tokens(f)
    assert any("compression" in t or "temperature" in t for t in tokens)


# ── signature 결정성 ─────────────────────────────────────────────────
def test_signature_is_deterministic_and_prefixed() -> None:
    d = _diff(["    x = generate(features, beam_size=5, patience=2.0)"])
    s1 = sig.compute_signature(sig.extract_features(d))
    s2 = sig.compute_signature(sig.extract_features(d))
    assert s1 == s2
    assert s1.startswith("sig_")


def test_identical_diffs_same_signature() -> None:
    d = _diff(["    suppress_tokens = build_latin_mask()"])
    assert sig.compute_signature(sig.extract_features(d)) == sig.compute_signature(
        sig.extract_features(d)
    )


# ── family grouping (Jaccard) ────────────────────────────────────────
def test_first_candidate_creates_new_family() -> None:
    f = sig.extract_features(_diff(["    beam_size=5"]))
    fid, is_new = sig.assign_family(f, {})
    assert is_new
    assert fid.startswith("family_")


def test_unrelated_diffs_get_distinct_families() -> None:
    f_decode = sig.extract_features(
        _diff(["    x = generate(features, beam_size=8, patience=2.0)"])
    )
    f_audio = sig.extract_features(
        _diff(["    audio = preemphasis(audio)", "    audio = channel_eq(audio)"])
    )
    fid1, _ = sig.assign_family(f_decode, {})
    known = {fid1: f_decode}
    fid2, is_new = sig.assign_family(f_audio, known)
    assert is_new
    assert fid2 != fid1


def test_similar_diffs_join_same_family() -> None:
    # 같은 계열(decode beam/patience sweep) — 값만 다름.
    f_a = sig.extract_features(
        _diff(["    x = generate(features, beam_size=5, patience=2.0)"])
    )
    f_b = sig.extract_features(
        _diff(["    x = generate(features, beam_size=8, patience=2.5)"])
    )
    fid_a, _ = sig.assign_family(f_a, {})
    fid_b, is_new = sig.assign_family(f_b, {fid_a: f_a})
    assert fid_b == fid_a
    assert not is_new


def test_rename_only_difference_absorbed() -> None:
    # helper 이름만 바뀌고 api/stage 토큰은 동일 → 같은 family 로 흡수.
    f_a = sig.extract_features(
        _diff(["    mask = build_suppress_mask()", "    suppress_tokens = mask"])
    )
    f_b = sig.extract_features(
        _diff(["    blk = build_suppress_mask()", "    suppress_tokens = blk"])
    )
    fid_a, _ = sig.assign_family(f_a, {})
    fid_b, is_new = sig.assign_family(f_b, {fid_a: f_a})
    assert fid_b == fid_a
    assert not is_new


def test_empty_diff_is_handled() -> None:
    f = sig.extract_features("")
    # 빈 diff 도 죽지 않고 결정적 signature 를 낸다.
    assert sig.compute_signature(f).startswith("sig_")
    fid, is_new = sig.assign_family(f, {})
    assert is_new


# ── 알려진 한계 (Step 5 cooldown 전에 보강) ─────────────────────────────
# signature 가 hard gate(cooldown)로 쓰이기 전까지는 다양성 지표 품질만 영향.
# 아래는 의도하는 미래 동작을 xfail(strict)로 고정 — 고쳐지면 자동으로 알림.

@pytest.mark.xfail(
    strict=True,
    reason="리뷰 #5: signature 가 주석/문자열을 무시해야 함(AST/tokenize). "
    "현재는 added text substring 매칭이라 주석 키워드도 feature 가 된다. Step 5 전 보강.",
)
def test_comment_only_keyword_is_not_a_feature() -> None:
    f = sig.extract_features(_diff(["    # uses temperature fallback and beam_size here"]))
    assert not f.api_keywords  # 주석만 바뀌면 feature 0 이어야 spoofing 불가


@pytest.mark.xfail(
    strict=True,
    reason="리뷰 #6: deletion-only diff 가 같은 함수면 region 토큰만 남아 서로 다른 "
    "ablation 이 false-merge 된다. removed 라인도 keyword/param 을 removed:* prefix 로 "
    "추출해야 한다. Step 5/Step 3(ablate) 전 보강.",
)
def test_deletion_only_diffs_stay_distinct() -> None:
    da = (
        "@@ -1,3 +1,2 @@ def transcribe(audio, sr):\n"
        "-    results = generate(features, beam_size=5)\n"
    )
    db = (
        "@@ -1,3 +1,2 @@ def transcribe(audio, sr):\n"
        "-    suppress_tokens = build_latin_mask()\n"
    )
    fa = sig.extract_features(da)
    fb = sig.extract_features(db)
    fid_a, _ = sig.assign_family(fa, {})
    fid_b, is_new = sig.assign_family(fb, {fid_a: fa})
    assert is_new and fid_b != fid_a
