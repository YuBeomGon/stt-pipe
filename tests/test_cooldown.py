"""``harness.cooldown`` — 반복 dead-end 집계 (proposal §8, Step 5 soft)."""

from __future__ import annotations

from harness import cooldown as cd


def _rec(sig, fam, decision="reject", tokens=None):
    return {
        "harness_signature": sig,
        "harness_family_id": fam,
        "final_decision": decision,
        "feature_tokens": tokens or [],
    }


def test_empty_records() -> None:
    s = cd.compute_cooldowns([])
    assert s.is_empty()
    assert cd.warning_block(s) == ""


def test_signature_cooled_after_two_rejects() -> None:
    recs = [_rec("sig_a", "family_001"), _rec("sig_a", "family_001")]
    s = cd.compute_cooldowns(recs)
    assert "sig_a" in s.cooled_signatures


def test_single_reject_not_cooled() -> None:
    s = cd.compute_cooldowns([_rec("sig_a", "family_001")])
    assert "sig_a" not in s.cooled_signatures


def test_keep_does_not_count() -> None:
    recs = [_rec("sig_a", "family_001", "keep"), _rec("sig_a", "family_001", "keep")]
    assert cd.compute_cooldowns(recs).is_empty()


def test_family_warned_after_five_rejects() -> None:
    recs = [_rec(f"sig_{i}", "family_001") for i in range(5)]
    s = cd.compute_cooldowns(recs)
    assert "family_001" in s.warned_families


def test_warning_block_lists_items() -> None:
    recs = [_rec("sig_a", "family_001"), _rec("sig_a", "family_001")]
    block = cd.warning_block(cd.compute_cooldowns(recs))
    assert "sig_a" in block and "Cooldown" in block


def test_micro_bank_counts_toward_cooldown() -> None:
    # 2026-06-04 R-C/F4: align 자석은 micro_bank 로 재분류돼 reject-only 카운트를
    # 빠져나갔다. micro_bank 도 non-improving 으로 세야 signature 가 cool 된다.
    recs = [
        _rec("sig_a", "family_001", "micro_bank"),
        _rec("sig_a", "family_002", "micro_bank"),
    ]
    s = cd.compute_cooldowns(recs)
    assert "sig_a" in s.cooled_signatures


def test_api_surface_cooled_across_distinct_families() -> None:
    # 같은 align 구조가 매번 새 family_id 로 갈라져도, feature_tokens 의 API surface
    # (align/rerank/nbest)를 거친 키로 세서 ≥3회면 경고한다(family 라벨 우회 방지).
    recs = [
        _rec(f"sig_{i}", f"family_{i:03d}", "micro_bank", tokens=["align", "rerank"])
        for i in range(3)
    ]
    s = cd.compute_cooldowns(recs)
    assert "align" in s.cooled_api_tokens and "rerank" in s.cooled_api_tokens
    # family 는 전부 distinct(각 1회)라 family 경고는 안 떠야 한다.
    assert not s.warned_families
    block = cd.warning_block(s)
    assert "align" in block


def test_non_api_tokens_not_cooled() -> None:
    # API vocab 이 아닌 토큰은 거친 키로 세지 않는다(노이즈 방지).
    recs = [
        _rec(f"sig_{i}", f"family_{i:03d}", "reject", tokens=["zzz_not_api"])
        for i in range(4)
    ]
    s = cd.compute_cooldowns(recs)
    assert not s.cooled_api_tokens
