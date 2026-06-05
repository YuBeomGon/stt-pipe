"""``harness.verify.run_verify`` 의 wall-clock 타임아웃 경로.

cap(= baseline_total × multiplier)은 사후 측정 게이트라, judge.evaluate 가
디코딩에서 hang 하면 report 가 안 나와 cap 체크에 도달조차 못 한다(2-3h hang).
verify 가 subprocess 에 timeout 을 걸어 cap + 로드 여유에서 강제 종료하는지 검증.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness import config as cfg
from harness.verify import VerifyConfig, run_verify


def _setup_repo(tmp_path: Path, *, baseline_t: float = 100.0) -> Path:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "transcribe.py").write_text(
        "def transcribe(audio, sr):\n    return ''\n", encoding="utf-8"
    )
    (tmp_path / "baseline").mkdir()
    (tmp_path / "baseline" / "target_cer.json").write_text(
        json.dumps({"total_inference_time_s": baseline_t, "baseline_cer": 0.4}),
        encoding="utf-8",
    )
    return tmp_path


def _config(repo: Path, mult: float = 3.0) -> VerifyConfig:
    return VerifyConfig(
        repo_root=repo,
        hyp_id="t",
        workspace_file=Path("workspace/transcribe.py"),
        baseline_file=Path("baseline/target_cer.json"),
        runs_dir=Path("runs"),
        runtime_hard_multiplier=mult,
    )


def test_timeout_rejects(tmp_path: Path, monkeypatch) -> None:
    repo = _setup_repo(tmp_path)

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="judge.evaluate", timeout=k.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_verify(_config(repo))
    assert result.ok is False
    assert "타임아웃" in (result.error or "")


def test_timeout_value_is_cap_plus_margin(tmp_path: Path, monkeypatch) -> None:
    repo = _setup_repo(tmp_path, baseline_t=100.0)
    seen = {}

    def fake_run(*a, **k):
        seen["timeout"] = k.get("timeout")
        raise subprocess.TimeoutExpired(cmd="x", timeout=k.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_verify(_config(repo, mult=3.0))
    # cap = 100 × 3.0 = 300 ; + margin
    assert seen["timeout"] == 300.0 + cfg.VERIFY_TIMEOUT_LOAD_MARGIN_S


def test_no_timeout_when_baseline_time_zero(tmp_path: Path, monkeypatch) -> None:
    """baseline total_inference_time_s 가 0/누락이면 timeout=None (디코딩 막지 않음)."""
    repo = _setup_repo(tmp_path, baseline_t=0.0)
    seen = {}

    class _FakeProc:
        returncode = 1
        stdout = ""
        stderr = "no report"

    def fake_run(*a, **k):
        # timeout=None 이면 실제 subprocess.run 은 TimeoutExpired 를 던지지 않는다.
        seen["timeout"] = k.get("timeout")
        return _FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_verify(_config(repo, mult=3.0))
    assert seen["timeout"] is None
