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
    # Keep/bank threshold used while σ is provisional (review F2 → 0.002).
    # The eval is deterministic (beam search, temperature=0), so there is no
    # run-to-run noise and σ is legitimately ~0/provisional — this fallback is
    # therefore not a noise guard but a "is it worth banking" floor. Lowered
    # from 0.01 to 0.002 so genuine sub-0.01 improvements (e.g. 0.169→0.161)
    # are kept and compounded instead of discarded. Trade-off: greedier descent
    # on the 11-file eval can overfit — watched via the job-end holdout check.
    absolute_delta_fallback: float = cfg.BANKING_ABSOLUTE_DELTA
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
