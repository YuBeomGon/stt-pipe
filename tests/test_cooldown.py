"""``harness.cooldown`` — 반복 dead-end 집계 (proposal §8, Step 5 soft)."""

from __future__ import annotations

from harness import cooldown as cd


def _rec(sig, fam, decision="reject"):
    return {"harness_signature": sig, "harness_family_id": fam, "final_decision": decision}


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
