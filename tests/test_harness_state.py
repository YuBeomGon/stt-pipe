"""tests/test_harness_state.py — set/lineage state persistence."""

from __future__ import annotations

from harness.state import HarnessState


def test_set_fields_default_and_roundtrip(tmp_path) -> None:
    s = HarnessState(job_id="phase3_015")
    assert s.set_id == 0
    assert s.set_phase == "idle"
    assert s.set_best_cer is None
    assert s.champion_ref == "champion"
    s.set_id = 2
    s.set_phase = "refine"
    s.set_best_cer = 0.176
    p = tmp_path / "state.json"
    s.save(p)
    back = HarnessState.load(p)
    assert back.set_id == 2
    assert back.set_phase == "refine"
    assert back.set_best_cer == 0.176


def test_old_state_file_without_set_fields_loads(tmp_path) -> None:
    p = tmp_path / "old.json"
    p.write_text('{"job_id": "old", "iteration": 5, "best_cer": 0.2}\n',
                 encoding="utf-8")
    s = HarnessState.load(p)
    assert s.set_phase == "idle"   # default applied
    assert s.iteration == 5
