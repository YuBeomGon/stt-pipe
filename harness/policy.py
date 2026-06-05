"""
harness/policy.py
Keep/reject/success policy for Phase 3 candidate runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from harness import config as cfg

DecisionStatus = Literal["keep", "reject", "success"]


@dataclass(frozen=True)
class PolicyConfig:
    # Banking floor (micro_bank "유망 reject" 경계) used while σ is provisional.
    # The eval is deterministic (beam search, temperature=0), so there is no
    # run-to-run noise and σ is legitimately ~0/provisional. NOTE: this is NOT
    # the keep threshold — see keep_delta_eps. is_micro_bank (runner) uses this
    # to decide whether a non-keep candidate is worth banking as parent material.
    absolute_delta_fallback: float = cfg.BANKING_ABSOLUTE_DELTA
    # best 포인터 전진(keep) 임계 — banking floor 와 분리(2026-06-04 R-A/F1).
    # 결정적 eval 이라 이 값 이상의 strict 개선이면 best 를 전진시킨다(monotone).
    # 0.002 로 묶여 있던 탓에 Δ0.0004 같은 실제 개선이 버려져 정체가 일부 artifact
    # 였다. micro_bank 경계(0.002)는 그대로 두고 keep 만 낮춘다.
    keep_delta_eps: float = cfg.KEEP_DELTA_EPS
    success_runtime_multiplier: float = 1.0


@dataclass(frozen=True)
class Decision:
    status: DecisionStatus
    candidate_cer: float
    best_cer: float | None
    delta_from_best: float | None
    threshold: float | None
    reason: str


def improvement_threshold(
    sigma: float | None,
    is_provisional: bool,
    config: PolicyConfig,
) -> float:
    """best 를 전진(keep)시키는 최소 개선 폭. σ 가 잠정/0(결정적 eval)이면
    `keep_delta_eps`(monotone) — banking floor(0.002)와 분리(2026-06-04 R-A/F1).
    σ 가 실측이면 2σ noise guard."""
    if sigma is None or sigma <= 0.0 or is_provisional:
        return config.keep_delta_eps
    return 2.0 * sigma


def decide_candidate(
    report: dict[str, Any],
    baseline: dict[str, Any],
    best_cer: float | None,
    sigma: float | None,
    sigma_is_provisional: bool = False,
    config: PolicyConfig | None = None,
) -> Decision:
    cfg = config or PolicyConfig()
    candidate_cer = float(report["corpus_cer"])
    if not math.isfinite(candidate_cer):
        return Decision(
            status="reject",
            candidate_cer=candidate_cer,
            best_cer=best_cer,
            delta_from_best=None,
            threshold=None,
            reason=f"non-finite corpus_cer: {candidate_cer!r}",
        )
    run_time = float(report.get("total_inference_time_s", 0.0) or 0.0)

    target_cer = float(baseline.get("target_cer", 0.0) or 0.0)
    baseline_time = float(baseline.get("total_inference_time_s", 0.0) or 0.0)
    time_budget = baseline_time * cfg.success_runtime_multiplier
    if target_cer > 0.0 and baseline_time > 0.0:
        if candidate_cer <= target_cer and run_time <= time_budget:
            return Decision(
                status="success",
                candidate_cer=candidate_cer,
                best_cer=best_cer,
                delta_from_best=None if best_cer is None else best_cer - candidate_cer,
                threshold=None,
                reason=(
                    f"target reached: corpus_cer {candidate_cer:.6f} <= "
                    f"{target_cer:.6f}, runtime {run_time:.1f}s <= {time_budget:.1f}s"
                ),
            )

    if best_cer is None:
        return Decision(
            status="keep",
            candidate_cer=candidate_cer,
            best_cer=None,
            delta_from_best=None,
            threshold=None,
            reason="first valid candidate",
        )

    delta = best_cer - candidate_cer
    threshold = improvement_threshold(sigma, sigma_is_provisional, cfg)
    if delta >= threshold:
        return Decision(
            status="keep",
            candidate_cer=candidate_cer,
            best_cer=best_cer,
            delta_from_best=delta,
            threshold=threshold,
            reason=f"meaningful improvement: Δcer {delta:.6f} >= {threshold:.6f}",
        )

    return Decision(
        status="reject",
        candidate_cer=candidate_cer,
        best_cer=best_cer,
        delta_from_best=delta,
        threshold=threshold,
        reason=f"not enough improvement: Δcer {delta:.6f} < {threshold:.6f}",
    )


def decide_promotion(
    report: dict[str, Any],
    baseline: dict[str, Any],
    champion_cer: float | None,
    sigma: float | None,
    sigma_is_provisional: bool = False,
    config: PolicyConfig | None = None,
) -> Decision:
    """Promotion gate: does this candidate beat the global champion?

    Identical semantics to the legacy ``decide_candidate`` — the global champion
    *is* the historical "best_cer". Named separately (HARNESS-REDESIGN §74) so the
    runner can pair it with ``decide_lineage_progress`` (the in-set comparison)
    and the two roles read distinctly.
    """
    return decide_candidate(
        report=report,
        baseline=baseline,
        best_cer=champion_cer,
        sigma=sigma,
        sigma_is_provisional=sigma_is_provisional,
        config=config,
    )
