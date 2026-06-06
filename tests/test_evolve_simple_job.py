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
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

    cfg = es.SimpleConfig(job_id="job", repo_root=repo,
                          candidate_cmd=f"{sys.executable} {stub}",
                          iters=3, explore=0.0, parent_policy="best")
    best_id = es.run_job(cfg)
    loaded = arch.load_archive(repo / "runs" / "job")
    assert len(loaded) == 3                  # exactly N, no hidden multiplier
    assert best_id == "0001"                 # 0.18 is the min


_YAML = (
    "```yaml\ncapability_investigated: a\nwhat_i_learned: b\n"
    "hypothesis: c\nfingerprint: [t]\n```"
)


class _FakeResult:
    def __init__(self, stdout, stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def test_run_job_normal_completion_records_status_completed(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    stub = repo / "stub.py"
    stub.write_text(
        "import pathlib\n"
        "p = pathlib.Path('workspace/transcribe.py')\n"
        "p.write_text('def transcribe(a, s):\\n    return \\'hi\\'\\n')\n"
        "print('```yaml\\ncapability_investigated: a\\nwhat_i_learned: b\\n"
        "hypothesis: c\\nfingerprint: [t]\\n```')\n"
    )

    class FakeVR:
        def __init__(self):
            self.ok, self.report, self.error = True, {"corpus_cer": 0.2}, None
    monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR())
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

    cfg = es.SimpleConfig(job_id="job", repo_root=repo,
                          candidate_cmd=f"{sys.executable} {stub}",
                          iters=1, explore=0.0, parent_policy="best")
    es.run_job(cfg)
    import json
    state = json.loads((repo / "runs" / "_summary" / "job_state.json").read_text())
    assert state["status"] == "completed"


def test_run_job_aborts_on_exhausted_rate_limit(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    job_dir = repo / "runs" / "job"

    # iter 0: a clean scored candidate becomes the preserved best.
    # iter 1: rate-limited through the whole ladder -> abort.
    state = {"phase": 0}
    sleeps: list[float] = []
    monkeypatch.setattr(es, "_sleep", lambda secs: sleeps.append(secs))

    def _fake_candidate(*, candidate_cmd, prompt, out_dir, repo_root, workspace_file):
        out_dir.mkdir(parents=True, exist_ok=True)
        if state["phase"] == 0:
            (repo / "workspace" / "transcribe.py").write_text(
                "def transcribe(a, s):\n    return 'hi'\n"
            )
            res = _FakeResult(_YAML)
        else:
            res = _FakeResult("usage limit reached")
        (out_dir / "claude_stdout.txt").write_text(res.stdout, encoding="utf-8")
        (out_dir / "claude_stderr.txt").write_text(res.stderr, encoding="utf-8")
        return res

    monkeypatch.setattr(es.cc, "run_candidate_command", _fake_candidate)

    class FakeVR:
        def __init__(self):
            self.ok, self.report, self.error = True, {"corpus_cer": 0.15}, None
    monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR())
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

    # run iter 0 alone first (phase 0 -> scored best)
    cfg0 = es.SimpleConfig(job_id="job", repo_root=repo, candidate_cmd="claude -p",
                           iters=1, explore=0.0, parent_policy="best")
    best0 = es.run_job(cfg0)
    assert best0 == "0000"
    assert len(arch.load_archive(job_dir)) == 1

    # now phase 1: rate-limited job -> abort, no bogus row, best preserved
    state["phase"] = 1
    cfg1 = es.SimpleConfig(job_id="job", repo_root=repo, candidate_cmd="claude -p",
                           iters=1, explore=0.0, parent_policy="best")
    best1 = es.run_job(cfg1)
    assert best1 == "0000"                     # prior best preserved
    assert len(arch.load_archive(job_dir)) == 1  # NO bogus row for aborted iter
    assert sleeps == [m * 60 for m in es.cc.RATE_LIMIT_BACKOFF_MIN]
    import json
    st = json.loads((repo / "runs" / "_summary" / "job_state.json").read_text())
    assert st["status"] == "aborted_rate_limit"
    assert st["best_hyp_id"] == "job/0000"


def test_cli_parses_knobs(monkeypatch):
    import scripts.evolve_simple as cli
    ns = cli.parse_args([
        "--job-id", "j", "--iters", "5", "--directive", "try vad",
        "--explore", "0.3", "--ban", "beam sweep", "--pin", "0002",
        "--parent-policy", "random",
    ])
    assert ns.iters == 5
    assert ns.explore == 0.3
    assert ns.parent_policy == "random"
    assert ns.ban == ["beam sweep"]
    assert ns.pin == "0002"
    # --holdout-every is GONE (holdout is now operator-manual)
    assert not hasattr(ns, "holdout_every")
