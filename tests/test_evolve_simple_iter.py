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
    # state file written with job-qualified best_hyp_id (Fix 2): the verify
    # report lives at runs/<job>/<cid>/score_report.json, and evaluate_holdout
    # resolves the anchor as runs/<best_hyp_id>/score_report.json — so the state
    # field must carry "<job>/<cid>", not the bare cid.
    state = json.loads((job_dir.parent / "_summary" / "job_state.json").read_text())
    assert state["best_hyp_id"] == f"job/{rec.id}"
    assert state["best_cer"] == 0.15


def test_err_tail_combines_error_and_stderr():
    out = es._err_tail("boom", "x" * 50 + "CUDA out of memory", limit=20)
    assert "boom" in out
    assert "CUDA out of memory" in out
    # bounded to ~limit chars of stderr tail
    assert "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" not in out


def test_run_iter_verify_fail_captures_error(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    job_dir = repo / "runs" / "job"
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
            self.ok = False
            self.report = None
            self.error = "boom"
            self.stderr = "traceback...\nRuntimeError: CUDA out of memory\n"
    monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR())
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

    cfg = es.SimpleConfig(
        job_id="job", repo_root=repo,
        candidate_cmd=f"{sys.executable} {stub}",
        explore=0.0, parent_policy="best", iters=1,
    )
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rec = es.run_iter(cfg, iteration=0, archive=[])
    assert rec.status == "rejected"
    # the appended record carries the verify failure error
    loaded = arch.load_archive(job_dir)
    assert loaded[-1].error is not None
    assert "CUDA out of memory" in loaded[-1].error
    # error written to disk for inspection
    verr = job_dir / rec.id / "verify_error.txt"
    assert verr.is_file()
    assert "CUDA out of memory" in verr.read_text(encoding="utf-8")
    # emitted log line shows the reason, not a bare "rejected"
    assert "CUDA out of memory" in buf.getvalue()


def test_run_job_never_invokes_holdout_and_emits_summary(tmp_path, monkeypatch):
    """The loop must NEVER unseal/evaluate the holdout. We trip the test if any
    subprocess targets evaluate_holdout, assert no <job>_holdout.jsonl sidecar is
    written, and verify the job-end summary line (with the manual-holdout hint)
    is emitted."""
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
            self.ok, self.report, self.error = True, {"corpus_cer": 0.18}, None
    monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR())
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

    real_run = subprocess.run

    def guarded_run(cmd, *a, **k):
        if any("evaluate_holdout" in str(part) for part in (cmd if isinstance(cmd, (list, tuple)) else [cmd])):
            raise AssertionError(f"loop must not invoke holdout: {cmd}")
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", guarded_run)

    cfg = es.SimpleConfig(job_id="job", repo_root=repo,
                          candidate_cmd=f"{sys.executable} {stub}",
                          iters=1, explore=0.0, parent_policy="best")
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        best_id = es.run_job(cfg)
    out = buf.getvalue()
    # no holdout sidecar written by the loop
    assert not (repo / "runs" / "_summary" / "job_holdout.jsonl").exists()
    # job-end summary reports the in-loop best and the manual-holdout hint
    assert f"best={best_id}" in out
    assert "cer=0.1800" in out
    assert "scripts/evaluate_holdout.py" in out
    # job_end log row carries best_cer but no holdout key
    log_rows = [json.loads(l) for l in
                (repo / "runs" / "_summary" / "job_log.jsonl")
                .read_text().splitlines() if l.strip()]
    job_end = [r for r in log_rows if r.get("event") == "job_end"]
    assert len(job_end) == 1
    assert job_end[0]["best_cer"] == 0.18
    assert "holdout" not in job_end[0]
