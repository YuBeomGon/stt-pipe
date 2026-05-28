"""End-to-end smoke for `judge.evaluate` with a fake transcribe.

Avoids loading the real Whisper backend; just verifies the pipeline writes
score_report.json + per_file.jsonl + diagnosis_report.json and ends in a
corpus_cer scalar on stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def fake_transcribe_module(tmp_path, monkeypatch):
    pkg = tmp_path / "_fake_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "transcribe.py").write_text(
        "import numpy as np\n"
        "def transcribe(audio: np.ndarray, sr: int) -> str:\n"
        "    return '안녕하세요'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return "_fake_pkg.transcribe:transcribe"


def test_evaluate_end_to_end(tmp_path, fake_transcribe_module):
    out = tmp_path / "score_report.json"
    cmd = [
        sys.executable, "-m", "judge.evaluate",
        "--batch", "AIG_녹취반출_20250715",
        "--transcribe", fake_transcribe_module,
        "--out", str(out),
    ]
    env = {
        "PYTHONPATH": f"{ROOT}:{tmp_path}",
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    # silero/librosa pull in CUDA; harmless in tests, but speeds up.
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, cwd=str(ROOT), check=False,
    )
    assert result.returncode == 0, result.stderr
    last = result.stdout.strip().splitlines()[-1]
    float(last)  # must be a number
    assert out.is_file()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["num_files"] == 11
    assert (out.parent / "per_file.jsonl").is_file()
    assert (out.parent / "diagnosis_report.json").is_file()
