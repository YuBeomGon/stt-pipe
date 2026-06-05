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

from harness.signature import API_VOCAB

# §8 MVP 임계: signature 2회, family 5회, API surface 3회.
SIGNATURE_REJECT_THRESHOLD: int = 2
FAMILY_REJECT_THRESHOLD: int = 5
API_TOKEN_THRESHOLD: int = 3

# "비개선(non-improving)" 으로 집계할 결정. **reject 뿐 아니라 micro_bank 도 포함**
# (2026-06-04 R-C/F4): align/rerank 같은 실패 자석이 한 축만 개선해 micro_bank 로
# 재분류되면 reject-only 카운트를 빠져나가, phase3_012 에서 9/21 iter 가 같은 dead-end
# 를 무한 재시도해도 cooldown 이 한 번도 안 떴다. micro_bank 는 parent 재료로는 남기되
# (combine/refine 이 *다르게* 활용), "같은 구조를 그대로 재시도" 는 막는다(soft).
_NON_IMPROVING = frozenset({"reject", "micro_bank"})

_API_SET = frozenset(API_VOCAB)


@dataclass(frozen=True)
class CooldownState:
    cooled_signatures: frozenset[str] = field(default_factory=frozenset)
    warned_families: frozenset[str] = field(default_factory=frozenset)
    cooled_api_tokens: frozenset[str] = field(default_factory=frozenset)

    def is_empty(self) -> bool:
        return not (
            self.cooled_signatures or self.warned_families or self.cooled_api_tokens
        )

    def as_list(self) -> list[str]:
        return (
            sorted(self.cooled_signatures)
            + sorted(self.warned_families)
            + sorted(self.cooled_api_tokens)
        )


def compute_cooldowns(
    records: list[dict[str, Any]],
    sig_threshold: int = SIGNATURE_REJECT_THRESHOLD,
    family_threshold: int = FAMILY_REJECT_THRESHOLD,
    api_threshold: int = API_TOKEN_THRESHOLD,
) -> CooldownState:
    """decisions.jsonl 레코드에서 반복 비개선(reject+micro_bank)된 signature/family/
    API-surface 를 집계. signature/family 는 토큰 rename 으로 우회되므로(같은 align
    구조가 매번 새 family_id), feature_tokens 의 API-surface(예: align/rerank/nbest)
    를 거친 키로 함께 센다 — family 라벨이 갈라져도 같은 backend 호출이 충돌하게."""
    sig_rej: Counter[str] = Counter()
    fam_rej: Counter[str] = Counter()
    api_rej: Counter[str] = Counter()
    for r in records:
        if r.get("final_decision") not in _NON_IMPROVING:
            continue
        sig = r.get("harness_signature")
        fam = r.get("harness_family_id")
        if sig:
            sig_rej[sig] += 1
        if fam:
            fam_rej[fam] += 1
        for tok in r.get("feature_tokens") or []:
            if tok in _API_SET:
                api_rej[tok] += 1
    return CooldownState(
        cooled_signatures=frozenset(s for s, c in sig_rej.items() if c >= sig_threshold),
        warned_families=frozenset(f for f, c in fam_rej.items() if c >= family_threshold),
        cooled_api_tokens=frozenset(t for t, c in api_rej.items() if c >= api_threshold),
    )


def warning_block(state: CooldownState) -> str:
    """prompt 에 넣을 soft warning. 빈 cooldown 이면 빈 문자열(섹션 생략)."""
    if state.is_empty():
        return ""
    lines = ["Cooldown — these repeatedly failed (reject/micro_bank); do NOT retry them as-is (soft):"]
    if state.cooled_signatures:
        lines.append(
            "- avoid signatures (≥2 non-improving): "
            + ", ".join(sorted(state.cooled_signatures))
        )
    if state.warned_families:
        lines.append(
            "- avoid families (≥5 non-improving), bring a genuinely new angle: "
            + ", ".join(sorted(state.warned_families))
        )
    if state.cooled_api_tokens:
        lines.append(
            "- these backend surfaces keep failing (≥3 non-improving across families) — "
            "stop circling them, try a different mechanism: "
            + ", ".join(sorted(state.cooled_api_tokens))
        )
    return "\n".join(lines)
