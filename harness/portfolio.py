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
# NOTE(리뷰 #2): del_ratio 단일 축이라 slot 이름을 `best_deletion` 으로 둔다.
# proposal §3 의 의미상 "coverage" 는 length_ratio.mean 1.0 근접 + audio_coverage_rate
# 조합이라야 맞고, 그 복합 coverage score 는 parent 선택이 실제로 이 slot 을 쓰는
# Step 2 에서 별도 정의한다. (Step 1 에서 이 slot 은 적재만 되고 소비되지 않음.)
AXES: tuple[tuple[str, str], ...] = (
    ("best_deletion", "error_breakdown.del_ratio"),
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


def _all_entries(p: "Portfolio") -> list[dict[str, Any]]:
    """family_best + metric_best + micro_bank 의 후보 entry 들(hyp_id 중복 제거)."""
    seen: dict[str, dict[str, Any]] = {}
    for e in list(p.family_best.values()) + list(p.metric_best.values()) + p.micro_bank:
        hyp = e.get("hyp_id")
        if hyp and hyp not in seen:
            seen[hyp] = e
    return list(seen.values())


def global_best_entry(p: "Portfolio") -> dict[str, Any] | None:
    """global_best hyp_id 의 entry. 없으면 최저 cer family_best entry 로 대체."""
    if p.global_best:
        for e in _all_entries(p):
            if e.get("hyp_id") == p.global_best:
                return e
    cands = [e for e in p.family_best.values() if isinstance(e.get("cer"), (int, float))]
    return min(cands, key=lambda e: e["cer"]) if cands else None


def feasibility(p: "Portfolio") -> dict[str, bool]:
    """scheduler 의 mode 가능 여부. refine: 재료 1+, combine: 서로 다른 family 2+,
    ablate: 다듬을 global_best 존재."""
    entries = _all_entries(p)
    distinct_families = {
        e.get("harness_family_id") for e in entries if e.get("harness_family_id")
    }
    return {
        "refine": bool(entries),
        "combine": len(distinct_families) >= 2,
        "ablate": global_best_entry(p) is not None,
    }


def parents_for_mode(p: "Portfolio", mode: str) -> list[dict[str, Any]]:
    """mode 별 parent entry 목록(prompt 주입용). MVP 규칙(proposal §4.4):
    refine/ablate=global_best 1개, combine=서로 다른 family 2개. explore/plateau/
    repair 는 portfolio parent 없음(repair 는 runner 가 실패 iter 에서 잡는다).

    **combine 호환성은 distinct-family MVP**: §4.5 의 diff touched-region overlap /
    same changed-param 충돌 / axis complement 검사는 아직 안 한다 — 서로 다른 family
    면 후보로 본다. 첫 run 의 combine_success_rate 를 보고 충돌 검사를 추가한다."""
    if mode in ("refine", "ablate"):
        gb = global_best_entry(p)
        return [gb] if gb else []
    if mode == "combine":
        # 서로 다른 family 에서 cer 낮은 순 2개 (MVP compatible: family 상이).
        by_family: dict[str, dict[str, Any]] = {}
        for e in sorted(
            _all_entries(p),
            key=lambda x: (x.get("cer") if isinstance(x.get("cer"), (int, float)) else 9e9),
        ):
            fid = e.get("harness_family_id")
            if fid and fid not in by_family:
                by_family[fid] = e
            if len(by_family) >= 2:
                break
        return list(by_family.values())[:2] if len(by_family) >= 2 else []
    return []


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

        # global_best — keep/success 를 그대로 미러링한다(리뷰 #3).
        # policy.decide_candidate 는 best 대비 개선일 때만 keep 을 내므로(success
        # 는 target 도달), 최신 keep/success 가 곧 최저 CER 이다 — state.record_best
        # 와 동일 의미. 직접 CER 비교는 family_best(=family_id keyed)를 hyp_id 로
        # 조회하던 버그였고, 정책 미러링이 의도이므로 비교를 제거한다.
        if decision_status in ("keep", "success") and cer is not None:
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
