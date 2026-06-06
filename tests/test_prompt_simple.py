from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import archive as arch
from harness import prompt_simple as ps


def _rec(cid, cer, fp, learned="learned-thing"):
    return arch.ArchiveRecord(
        id=cid, parents=[], cer=cer, status="scored", hypothesis="h",
        what_i_learned=learned, fingerprint=fp, score_report=None, ts="t",
    )


def test_candidate_simple_md_exists_and_keeps_contract():
    body = (ROOT / "harness" / "prompts" / "candidate_simple.md").read_text()
    assert "Required output format" in body
    assert "fingerprint" in body
    # mode/family machinery removed
    assert "injects a dedicated mode block" not in body
    assert "groups candidates into **families**" not in body


def test_build_prompt_contains_all_sections():
    parent = _rec("0001", 0.18, ["vad"])
    inspr = [_rec("0002", 0.17, ["rover"], learned="rover helps"),
             _rec("0003", 0.19, ["beam"])]
    prompt = ps.build_simple_prompt(
        profile="PROFILE-BODY",
        frozen_surface="FROZEN-SURFACE",
        workspace_body="def transcribe(a, s): return ''",
        baseline={"target_cer": 0.10, "total_inference_time_s": 152.3},
        mode="EXPLORE",
        directive="try VAD segmentation",
        bans=["beam sweep"],
        parent=parent,
        inspirations=inspr,
        allowed_path="workspace/transcribe.py",
        best_cer=0.17, best_hyp_id="0002",
    )
    assert "PROFILE-BODY" in prompt
    assert "FROZEN-SURFACE" in prompt
    assert "try VAD segmentation" in prompt          # directive slot
    assert "beam sweep" in prompt                     # ban block
    assert "EXPLORE" in prompt                        # mode directive
    assert "0001" in prompt and "0.18" in prompt      # parent block
    assert "rover helps" in prompt                    # learnings ledger
    assert not prompt.startswith("-")                 # argv-safe first char


def _failed_rec(cid, error, hypothesis="batch all windows in one generate() call"):
    return arch.ArchiveRecord(
        id=cid, parents=[], cer=None, status="rejected", hypothesis=hypothesis,
        what_i_learned="", fingerprint=["batch-windows"], score_report=None,
        ts="t", error=error,
    )


def test_build_prompt_includes_recent_failures_section():
    archive = [
        _rec("0000", 0.20, ["base"]),
        _failed_rec("0001", "CUDA out of memory while batching 200 windows"),
    ]
    prompt = ps.build_simple_prompt(
        profile="P", frozen_surface="F", workspace_body="w",
        baseline={"target_cer": 0.1, "total_inference_time_s": 1.0},
        mode="EXPLORE", directive="", bans=[], parent=None, inspirations=[],
        allowed_path="workspace/transcribe.py", best_cer=0.20, best_hyp_id="0000",
        archive=archive,
    )
    assert "RECENT FAILURES" in prompt
    assert "CUDA out of memory while batching 200 windows" in prompt
    # the failing approach gist surfaces so the LLM maps approach -> error
    assert "batch all windows" in prompt
    # uses === separators, never a leading --- line
    assert "\n---\n" not in prompt


def test_build_prompt_no_failures_section_when_none():
    archive = [_rec("0000", 0.20, ["base"]), _rec("0001", 0.18, ["vad"])]
    prompt = ps.build_simple_prompt(
        profile="P", frozen_surface="F", workspace_body="w",
        baseline={"target_cer": 0.1, "total_inference_time_s": 1.0},
        mode="EXPLORE", directive="", bans=[], parent=None, inspirations=[],
        allowed_path="workspace/transcribe.py", best_cer=0.18, best_hyp_id="0001",
        archive=archive,
    )
    assert "RECENT FAILURES" not in prompt


def test_build_prompt_exploit_directive_differs():
    explore = ps.build_simple_prompt(
        profile="P", frozen_surface="F", workspace_body="w",
        baseline={"target_cer": 0.1, "total_inference_time_s": 1.0},
        mode="EXPLORE", directive="", bans=[], parent=None, inspirations=[],
        allowed_path="workspace/transcribe.py", best_cer=None, best_hyp_id=None,
    )
    exploit = ps.build_simple_prompt(
        profile="P", frozen_surface="F", workspace_body="w",
        baseline={"target_cer": 0.1, "total_inference_time_s": 1.0},
        mode="EXPLOIT", directive="", bans=[], parent=None, inspirations=[],
        allowed_path="workspace/transcribe.py", best_cer=None, best_hyp_id=None,
    )
    assert "EXPLORE MODE" in explore
    assert "EXPLOIT MODE" in exploit
