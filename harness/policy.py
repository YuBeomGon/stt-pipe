"""
harness/policy.py
Keep/reject/success policy for Phase 3 candidate runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

DecisionStatus = Literal["keep", "reject", "success"]


@dataclass(frozen=True)
class PolicyConfig:
    absolute_delta_fallback: float = 0.01
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
    if sigma is None or sigma <= 0.0 or is_provisional:
        return config.absolute_delta_fallback
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

