"""
harness/cooldown.py
Repeated dead-end detection from the harness decision trace (proposal §8).

candidate 자기보고 fingerprint 는 토큰 rename 으로 우회되므로, cooldown 의 정본은
harness-derived `harness_signature` / `harness_family_id` 다(decisions.jsonl 에 기록됨).

**Step 5 MVP 는 soft 다**: cooldown 은 prompt warning("이 family/signature 는 반복
실패했으니 피하라")으로만 노출하고, verify 전 hard reject 는 하지 않는다. proposal §8
이 "family clustering hard gate 는 사후 false-positive 확인 후 강화" 라고 못박았으므로,
첫 run 의 decisions.jsonl 로 오탐을 본 뒤 hard gate 를 켠다. 전부 순수함수.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# §8 MVP 임계: signature 2회 reject → cooldown 후보, family 5회 reject → warning.
SIGNATURE_REJECT_THRESHOLD: int = 2
FAMILY_REJECT_THRESHOLD: int = 5


@dataclass(frozen=True)
class CooldownState:
    cooled_signatures: frozenset[str] = field(default_factory=frozenset)
    warned_families: frozenset[str] = field(default_factory=frozenset)

    def is_empty(self) -> bool:
        return not self.cooled_signatures and not self.warned_families

    def as_list(self) -> list[str]:
        return sorted(self.cooled_signatures) + sorted(self.warned_families)


def compute_cooldowns(
    records: list[dict[str, Any]],
    sig_threshold: int = SIGNATURE_REJECT_THRESHOLD,
    family_threshold: int = FAMILY_REJECT_THRESHOLD,
) -> CooldownState:
    """decisions.jsonl 레코드에서 반복 reject 된 signature/family 집계."""
    sig_rej: Counter[str] = Counter()
    fam_rej: Counter[str] = Counter()
    for r in records:
        if r.get("final_decision") != "reject":
            continue
        sig = r.get("harness_signature")
        fam = r.get("harness_family_id")
        if sig:
            sig_rej[sig] += 1
        if fam:
            fam_rej[fam] += 1
    return CooldownState(
        cooled_signatures=frozenset(s for s, c in sig_rej.items() if c >= sig_threshold),
        warned_families=frozenset(f for f, c in fam_rej.items() if c >= family_threshold),
    )


def warning_block(state: CooldownState) -> str:
    """prompt 에 넣을 soft warning. 빈 cooldown 이면 빈 문자열(섹션 생략)."""
    if state.is_empty():
        return ""
    lines = ["Cooldown — these repeatedly failed; do NOT retry them as-is (soft):"]
    if state.cooled_signatures:
        lines.append(
            "- avoid signatures (≥2 rejects): "
            + ", ".join(sorted(state.cooled_signatures))
        )
    if state.warned_families:
        lines.append(
            "- avoid families (≥5 rejects), bring a genuinely new angle: "
            + ", ".join(sorted(state.warned_families))
        )
    return "\n".join(lines)
