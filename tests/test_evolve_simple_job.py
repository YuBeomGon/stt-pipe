from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import archive as arch
from harness import evolve_simple as es


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "transcribe.py").write_text("def transcribe(a, s):\n    return ''\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_run_job_produces_exactly_n_rows(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    stub = repo / "stub.py"
    stub.write_text(
        "import pathlib\n"
        "p = pathlib.Path('workspace/transcribe.py')\n"
        "p.write_text('def transcribe(a, s):\\n    return \\'hi\\'\\n')\n"
        "print('```yaml\\ncapability_investigated: a\\nwhat_i_learned: b\\n"
        "hypothesis: c\\nfingerprint: [t]\\n```')\n"
    )
    cers = iter([0.20, 0.18, 0.19])

    class FakeVR:
        def __init__(self, cer):
            self.ok, self.report, self.error = True, {"corpus_cer": cer}, None
    monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR(next(cers)))
    # default holdout_every=0 still runs a job-end holdout on the final best;
    # stub it so the test never touches the real sealed corpus.
    monkeypatch.setattr(es, "_invoke_holdout", lambda cfg_, best_id: None)
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

    cfg = es.SimpleConfig(job_id="job", repo_root=repo,
                          candidate_cmd=f"{sys.executable} {stub}",
                          iters=3, explore=0.0, parent_policy="best")
    best_id = es.run_job(cfg)
    loaded = arch.load_archive(repo / "runs" / "job")
    assert len(loaded) == 3                  # exactly N, no hidden multiplier
    assert best_id == "0001"                 # 0.18 is the min


def test_cli_parses_six_knobs(monkeypatch):
    import scripts.evolve_simple as cli
    ns = cli.parse_args([
        "--job-id", "j", "--iters", "5", "--directive", "try vad",
        "--explore", "0.3", "--ban", "beam sweep", "--pin", "0002",
        "--holdout-every", "2", "--parent-policy", "random",
    ])
    assert ns.iters == 5
    assert ns.explore == 0.3
    assert ns.parent_policy == "random"
    assert ns.ban == ["beam sweep"]
    assert ns.pin == "0002"
    assert ns.holdout_every == 2
