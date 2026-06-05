from __future__ import annotations

import json
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


def _coin_is_deterministic():
    assert es._coin("job", 5) == es._coin("job", 5)


def test_coin_deterministic_and_in_unit_interval():
    v = es._coin("phase", 3)
    assert 0.0 <= v < 1.0
    assert es._coin("phase", 3) == v


def test_scope_ok_ignores_preexisting_rejects_new(tmp_path):
    repo = _git_repo(tmp_path)
    wp = Path("workspace/transcribe.py")
    (repo / "workspace" / "transcribe.py").write_text("def transcribe(a,s):\n return 'x'\n")
    # repo is ALREADY dirty with an out-of-scope untracked file (like the real
    # operator repo with docs/proposals). This must NOT cause a reject.
    (repo / "preexisting.md").write_text("untracked doc")
    before = es._out_of_scope(repo, wp)
    # candidate edits ONLY workspace + writes under runs/ (in scope)
    (repo / "runs" / "job" / "0000").mkdir(parents=True)
    (repo / "runs" / "job" / "0000" / "x.txt").write_text("ok")
    (repo / "workspace" / "transcribe.py").write_text("def transcribe(a,s):\n return 'y'\n")
    assert es._scope_ok(before, es._out_of_scope(repo, wp)) is True
    # candidate introduces a NEW out-of-scope file -> delta non-empty -> reject
    (repo / "evil.py").write_text("nope")
    assert es._scope_ok(before, es._out_of_scope(repo, wp)) is False


def test_run_iter_always_appends_and_keeps_if_better(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    job_dir = repo / "runs" / "job"
    # stub candidate edits the workspace + prints a valid YAML block
    stub = repo / "stub.py"
    stub.write_text(
        "import pathlib\n"
        "p = pathlib.Path('workspace/transcribe.py')\n"
        "p.write_text('def transcribe(a, s):\\n    return \\'hi\\'\\n')\n"
        "print('```yaml\\ncapability_investigated: a\\nwhat_i_learned: b\\n"
        "hypothesis: c\\nfingerprint: [t]\\n```')\n"
    )
    # fake verify: monkeypatch run_verify to return a scored result
    from harness import verify as vmod  # noqa: F401

    class FakeVR:
        def __init__(self, cer):
            self.ok = True
            self.report = {"corpus_cer": cer}
            self.error = None
    monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR(0.15))

    cfg = es.SimpleConfig(
        job_id="job", repo_root=repo,
        candidate_cmd=f"{sys.executable} {stub}",
        explore=0.0, parent_policy="best", iters=1,
    )
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")
    rec = es.run_iter(cfg, iteration=0, archive=[])
    assert rec.status == "scored"
    loaded = arch.load_archive(job_dir)
    assert len(loaded) == 1                     # ALWAYS appended
    assert arch.read_best(job_dir) == rec.id    # first scored -> best
    # workspace restored to committed stub
    assert "return ''" in (repo / "workspace" / "transcribe.py").read_text()
    # state file written with best_hyp_id
    state = json.loads((job_dir.parent / "_summary" / "job_state.json").read_text())
    assert state["best_hyp_id"] == rec.id
    assert state["best_cer"] == 0.15
