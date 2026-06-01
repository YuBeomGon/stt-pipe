"""
harness/portfolio.py
Portfolio of evolution material across multiple axes (proposal §3).

single-best hill-climb 대신, global best 를 못 이겨도 한 축을 개선한 후보를
보존해 이후 refine/combine/ablate 의 재료로 쓴다. Step 1 에서는 **저장만** 한다
(prompt 주입·parent 선택은 Step 2+). 기존 keep/reject 판정(`policy.decide_candidate`)
은 건드리지 않는다 — 본 모듈은 그 결정을 미러링해 재료를 적재할 뿐이다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# metric_best 축: (slot 이름, score_report dotted key, "lower better"). proposal §3 +
# §14 (judge corpus_aggregate 가 실제로 내는 키). 전부 낮을수록 좋음.
AXES: tuple[tuple[str, str], ...] = (
    ("best_coverage", "error_breakdown.del_ratio"),
    ("best_substitution", "error_breakdown.sub_ratio"),
    ("best_low_hallucination", "hallucination_hit_rate"),
    ("fast_runtime_variant", "total_inference_time_s"),
)
# 축 개선으로 인정할 최소 폭 (noise guard).
_AXIS_EPS: float = 1e-4


def axis_value(report: dict[str, Any], dotted: str) -> float | None:
    cur: Any = report
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def is_micro_bank(
    report: dict[str, Any],
    best_report: dict[str, Any] | None,
    keep_threshold: float,
) -> tuple[bool, str]:
    """policy 가 reject 한 후보가 진화 재료로 보존할 가치가 있는가.

    (a) strict CER 개선이지만 keep threshold 미만이거나, (b) 한 축(sub/del/
    hallucination/runtime)을 best 대비 개선했으면 micro_bank. keep 로직과 무관한
    순수 판정이라 runner 가 reject 분기에서만 호출한다.
    """
    if best_report is None:
        return False, "no global best yet"
    cand_cer = axis_value(report, "corpus_cer")
    best_cer = axis_value(best_report, "corpus_cer")
    if cand_cer is not None and best_cer is not None:
        delta = best_cer - cand_cer
        if 0.0 < delta < keep_threshold:
            return True, f"strict cer improvement {delta:.6f} < keep {keep_threshold:.6f}"
    for _slot, key in AXES:
        cv = axis_value(report, key)
        bv = axis_value(best_report, key)
        if cv is not None and bv is not None and cv < bv - _AXIS_EPS:
            return True, f"axis improved: {key} {cv:.6f} < {bv:.6f}"
    return False, "no micro improvement"


def improved_axes(
    report: dict[str, Any], best_report: dict[str, Any] | None
) -> list[str]:
    """best 대비 개선한 축 slot 이름들 (rejected_promising 분류용)."""
    if best_report is None:
        return []
    out = []
    for slot, key in AXES:
        cv = axis_value(report, key)
        bv = axis_value(best_report, key)
        if cv is not None and bv is not None and cv < bv - _AXIS_EPS:
            out.append(slot)
    return out


def _entry(
    hyp_id: str,
    iteration: int,
    status: str,
    report: dict[str, Any],
    *,
    harness_signature: str,
    harness_family_id: str,
    self_declared_family_id: str | None,
    fingerprint: list[str] | None,
    mode: str | None,
    diff_path: str | None,
) -> dict[str, Any]:
    return {
        "hyp_id": hyp_id,
        "iter": iteration,
        "status": status,
        "cer": axis_value(report, "corpus_cer"),
        "harness_signature": harness_signature,
        "harness_family_id": harness_family_id,
        "self_declared_family_id": self_declared_family_id,
        "fingerprint": fingerprint or [],
        "mode": mode,
        "diff_path": diff_path,
        "runtime_s": axis_value(report, "total_inference_time_s"),
        "axis_metric": {key: axis_value(report, key) for _slot, key in AXES},
    }


@dataclass
class Portfolio:
    job_id: str
    updated_at_iter: int = 0
    global_best: str | None = None
    family_best: dict[str, dict[str, Any]] = field(default_factory=dict)
    metric_best: dict[str, dict[str, Any]] = field(default_factory=dict)
    micro_bank: list[dict[str, Any]] = field(default_factory=list)
    rejected_promising: list[dict[str, Any]] = field(default_factory=list)

    # ── persistence (state.py 와 동일 atomic write) ──────────────────
    @classmethod
    def load(cls, path: Path) -> "Portfolio":
        if not path.is_file():
            # job_id 는 caller 가 채운다; 파일명에 묶이지 않게 placeholder.
            return cls(job_id="")
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {
            "job_id", "updated_at_iter", "global_best", "family_best",
            "metric_best", "micro_bank", "rejected_promising",
        }
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    # ── update ───────────────────────────────────────────────────────
    def update(
        self,
        *,
        hyp_id: str,
        iteration: int,
        decision_status: str,  # keep | success | micro_bank | reject
        report: dict[str, Any],
        best_report: dict[str, Any] | None,
        harness_signature: str,
        harness_family_id: str,
        self_declared_family_id: str | None = None,
        fingerprint: list[str] | None = None,
        mode: str | None = None,
        diff_path: str | None = None,
    ) -> list[str]:
        """후보 1개를 portfolio 에 반영. 갱신된 slot 이름 목록을 반환(로그용).

        decision_status 는 runner 의 정책 결정을 그대로 받는다 — 본 모듈이
        keep/reject 를 새로 판단하지 않는다.
        """
        self.updated_at_iter = iteration
        updated: list[str] = []
        entry = _entry(
            hyp_id, iteration, decision_status, report,
            harness_signature=harness_signature,
            harness_family_id=harness_family_id,
            self_declared_family_id=self_declared_family_id,
            fingerprint=fingerprint,
            mode=mode,
            diff_path=diff_path,
        )
        cer = entry["cer"]
        valid = decision_status in ("keep", "success", "micro_bank")

        # global_best — keep/success 만, CER 최저.
        if decision_status in ("keep", "success") and cer is not None:
            cur = self.family_best.get(self.global_best, {}).get("cer") if self.global_best else None
            if self.global_best is None or cur is None or cer <= cur:
                self.global_best = hyp_id
                updated.append("global_best")

        # family_best — valid 후보, family 별 CER 최저.
        if valid and cer is not None:
            prev = self.family_best.get(harness_family_id)
            if prev is None or prev.get("cer") is None or cer <= prev["cer"]:
                self.family_best[harness_family_id] = entry
                updated.append(f"family_best:{harness_family_id}")

        # metric_best — valid 후보, 축별 최저.
        if valid:
            for slot, key in AXES:
                v = axis_value(report, key)
                if v is None:
                    continue
                prev = self.metric_best.get(slot)
                pv = prev.get("axis_metric", {}).get(key) if prev else None
                if prev is None or pv is None or v < pv:
                    self.metric_best[slot] = entry
                    updated.append(f"metric_best:{slot}")

        # micro_bank — 명시적 micro_bank 결정.
        if decision_status == "micro_bank":
            self.micro_bank.append(entry)
            updated.append("micro_bank")

        # rejected_promising — reject 인데 축 개선이 있으면.
        if decision_status == "reject":
            axes = improved_axes(report, best_report)
            if axes:
                e = dict(entry)
                e["improved_axes"] = axes
                self.rejected_promising.append(e)
                updated.append("rejected_promising")

        return updated
