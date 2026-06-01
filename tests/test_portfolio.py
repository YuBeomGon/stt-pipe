"""``harness.portfolio`` — 진화 재료 적재 (proposal §3/§8).

keep/reject 판정은 미러링만 하고, micro_bank/축개선 보존 로직을 고정한다.
"""

from __future__ import annotations

from harness import portfolio as pf
from harness.portfolio import Portfolio


def _report(cer, sub=0.3, dele=0.3, hall=0.1, rt=400.0):
    return {
        "corpus_cer": cer,
        "error_breakdown": {"sub_ratio": sub, "del_ratio": dele, "ins_ratio": 0.1},
        "hallucination_hit_rate": hall,
        "total_inference_time_s": rt,
    }


# ── axis_value ───────────────────────────────────────────────────────
def test_axis_value_dotted_and_missing() -> None:
    r = _report(0.2)
    assert pf.axis_value(r, "corpus_cer") == 0.2
    assert pf.axis_value(r, "error_breakdown.sub_ratio") == 0.3
    assert pf.axis_value(r, "nope.nope") is None


# ── is_micro_bank ────────────────────────────────────────────────────
def test_micro_bank_strict_cer_below_threshold() -> None:
    best = _report(0.2000)
    cand = _report(0.1997)  # Δ0.0003, keep threshold 0.002 미만
    ok, _ = pf.is_micro_bank(cand, best, keep_threshold=0.002)
    assert ok


def test_micro_bank_axis_improvement_even_if_cer_worse() -> None:
    best = _report(0.20, sub=0.30)
    cand = _report(0.21, sub=0.25)  # CER 나쁘지만 sub 개선
    ok, reason = pf.is_micro_bank(cand, best, keep_threshold=0.002)
    assert ok
    assert "sub_ratio" in reason


def test_not_micro_bank_when_nothing_improves() -> None:
    best = _report(0.20, sub=0.30, dele=0.30, hall=0.1, rt=400)
    cand = _report(0.25, sub=0.40, dele=0.40, hall=0.2, rt=500)
    ok, _ = pf.is_micro_bank(cand, best, keep_threshold=0.002)
    assert not ok


def test_no_best_yet_is_not_micro_bank() -> None:
    ok, _ = pf.is_micro_bank(_report(0.2), None, keep_threshold=0.002)
    assert not ok


# ── Portfolio.update ─────────────────────────────────────────────────
def _kw(**over):
    base = dict(
        hyp_id="phase3_006_iter_001",
        iteration=1,
        decision_status="keep",
        report=_report(0.18),
        best_report=None,
        harness_signature="sig_a",
        harness_family_id="family_001",
    )
    base.update(over)
    return base


def test_update_keep_sets_global_and_family_and_metric() -> None:
    p = Portfolio(job_id="phase3_006")
    updated = p.update(**_kw())
    assert p.global_best == "phase3_006_iter_001"
    assert "family_001" in p.family_best
    assert "global_best" in updated
    assert any(s.startswith("metric_best:") for s in updated)


def test_update_micro_bank_appends_material_not_global() -> None:
    p = Portfolio(job_id="phase3_006")
    p.update(**_kw())  # keep → global
    updated = p.update(
        **_kw(
            hyp_id="phase3_006_iter_002",
            iteration=2,
            decision_status="micro_bank",
            report=_report(0.181),
            harness_family_id="family_002",
        )
    )
    assert "micro_bank" in updated
    assert len(p.micro_bank) == 1
    assert p.global_best == "phase3_006_iter_001"  # 안 바뀜


def test_update_reject_with_axis_gain_goes_to_promising() -> None:
    p = Portfolio(job_id="phase3_006")
    p.update(**_kw(report=_report(0.18, sub=0.30)))
    best = _report(0.18, sub=0.30)
    updated = p.update(
        **_kw(
            hyp_id="phase3_006_iter_003",
            iteration=3,
            decision_status="reject",
            report=_report(0.20, sub=0.20),  # CER 나쁘지만 sub 개선
            best_report=best,
        )
    )
    assert "rejected_promising" in updated
    assert p.rejected_promising[0]["improved_axes"] == ["best_substitution"]


def test_update_reject_plain_changes_nothing() -> None:
    p = Portfolio(job_id="phase3_006")
    p.update(**_kw())
    best = _report(0.18)
    updated = p.update(
        **_kw(
            hyp_id="phase3_006_iter_004",
            iteration=4,
            decision_status="reject",
            report=_report(0.30, sub=0.5, dele=0.5, hall=0.3, rt=900),
            best_report=best,
        )
    )
    assert updated == []


# ── persistence ──────────────────────────────────────────────────────
def test_save_load_roundtrip(tmp_path) -> None:
    p = Portfolio(job_id="phase3_006")
    p.update(**_kw())
    path = tmp_path / "phase3_006_portfolio.json"
    p.save(path)
    q = Portfolio.load(path)
    q.job_id = "phase3_006"
    assert q.global_best == p.global_best
    assert q.family_best.keys() == p.family_best.keys()


def test_load_missing_file() -> None:
    from pathlib import Path

    q = Portfolio.load(Path("/nonexistent/phase3_006_portfolio.json"))
    assert q.global_best is None
