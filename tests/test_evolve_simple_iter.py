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


def _seed_best(tmp_path: Path, best_body: str = "def transcribe(a, s):\n    return 'best'\n") -> Path:
    """Create a job dir with one scored record + best.txt + the best snapshot
    (runs/job/0000/transcribe.py) so _maybe_holdout can materialize it. The
    workspace stub differs from best_body so tests can detect materialization."""
    job_dir = tmp_path / "runs" / "job"
    arch.append_record(job_dir, arch.ArchiveRecord(
        id="0000", parents=[], cer=0.15, status="scored", hypothesis="h",
        what_i_learned="l", fingerprint=["t"], score_report=None, ts="t"))
    arch.write_best(job_dir, "0000")
    (job_dir / "0000").mkdir(parents=True, exist_ok=True)
    (job_dir / "0000" / "transcribe.py").write_text(best_body, encoding="utf-8")
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "transcribe.py").write_text("def transcribe(a, s):\n    return 'stub'\n",
                                      encoding="utf-8")
    return job_dir


def test_maybe_holdout_records_sidecar_on_cadence(tmp_path, monkeypatch):
    _seed_best(tmp_path)
    cfg = es.SimpleConfig(job_id="job", repo_root=tmp_path, holdout_every=1)
    # fake the holdout subprocess: return a fixed cer for the current best
    # (NEVER touch the real sealed corpus from a test).
    monkeypatch.setattr(es, "_invoke_holdout", lambda cfg_, best_id: 0.16)
    archive = arch.load_archive(tmp_path / "runs" / "job")
    es._maybe_holdout(cfg, iteration=0, archive=archive)
    sidecar = (tmp_path / "runs" / "_summary" / "job_holdout.jsonl")
    assert sidecar.is_file()
    rows = [json.loads(l) for l in sidecar.read_text().splitlines() if l.strip()]
    assert rows[-1]["hyp_id"] == "0000"
    assert rows[-1]["holdout_cer"] == 0.16


def test_maybe_holdout_dedups_per_best_id(tmp_path, monkeypatch):
    _seed_best(tmp_path)
    cfg = es.SimpleConfig(job_id="job", repo_root=tmp_path, holdout_every=1)
    calls = []
    monkeypatch.setattr(es, "_invoke_holdout",
                        lambda cfg_, best_id: calls.append(best_id) or 0.16)
    archive = arch.load_archive(tmp_path / "runs" / "job")
    es._maybe_holdout(cfg, iteration=0, archive=archive)
    es._maybe_holdout(cfg, iteration=1, archive=archive)  # same best -> skip
    assert calls == ["0000"]  # invoked exactly once for this best id
    rows = [json.loads(l) for l in
            (tmp_path / "runs" / "_summary" / "job_holdout.jsonl")
            .read_text().splitlines() if l.strip()]
    assert len(rows) == 1


def test_maybe_holdout_no_best_is_noop(tmp_path, monkeypatch):
    cfg = es.SimpleConfig(job_id="job", repo_root=tmp_path, holdout_every=1)
    called = []
    monkeypatch.setattr(es, "_invoke_holdout",
                        lambda cfg_, best_id: called.append(best_id))
    es._maybe_holdout(cfg, iteration=0, archive=[])  # empty archive
    assert called == []
    assert not (tmp_path / "runs" / "_summary" / "job_holdout.jsonl").exists()


def test_holdout_never_influences_best_selection(tmp_path, monkeypatch):
    """The holdout cer (here much WORSE than in-loop) must not change best.txt
    or which record best_record returns — selection is in-loop cer only."""
    _seed_best(tmp_path)
    cfg = es.SimpleConfig(job_id="job", repo_root=tmp_path, holdout_every=1)
    monkeypatch.setattr(es, "_invoke_holdout", lambda cfg_, best_id: 0.99)
    archive = arch.load_archive(tmp_path / "runs" / "job")
    es._maybe_holdout(cfg, iteration=0, archive=archive)
    assert arch.read_best(tmp_path / "runs" / "job") == "0000"
    assert arch.best_record(arch.load_archive(tmp_path / "runs" / "job")).id == "0000"


def test_maybe_holdout_materializes_best_before_invoke_and_restores(tmp_path, monkeypatch):
    """Fix 1: the holdout must transcribe with the BEST candidate's code, not the
    stub that run_iter leaves in the workspace. _maybe_holdout must materialize
    the best snapshot into workspace/transcribe.py BEFORE invoking the holdout,
    and restore the committed stub AFTER."""
    repo = _git_repo(tmp_path)
    # seed best with a DISTINCT body so we can tell best vs committed stub
    job_dir = repo / "runs" / "job"
    arch.append_record(job_dir, arch.ArchiveRecord(
        id="0000", parents=[], cer=0.15, status="scored", hypothesis="h",
        what_i_learned="l", fingerprint=["t"], score_report=None, ts="t"))
    arch.write_best(job_dir, "0000")
    (job_dir / "0000").mkdir(parents=True, exist_ok=True)
    best_body = "def transcribe(a, s):\n    return 'BEST'\n"
    (job_dir / "0000" / "transcribe.py").write_text(best_body, encoding="utf-8")

    ws_file = repo / "workspace" / "transcribe.py"
    seen = {}

    def fake_invoke(cfg_, best_id):
        # at invoke time the workspace MUST hold the best snapshot, not the stub
        seen["at_invoke"] = ws_file.read_text(encoding="utf-8")
        return 0.16
    monkeypatch.setattr(es, "_invoke_holdout", fake_invoke)

    cfg = es.SimpleConfig(job_id="job", repo_root=repo, holdout_every=1)
    archive = arch.load_archive(job_dir)
    es._maybe_holdout(cfg, iteration=0, archive=archive)

    assert seen["at_invoke"] == best_body            # materialized best at invoke
    # restored to the committed stub afterwards
    assert "return ''" in ws_file.read_text(encoding="utf-8")


def test_maybe_holdout_restores_even_if_invoke_raises(tmp_path, monkeypatch):
    """Fix 1: restore must happen in a finally — even if the holdout raises."""
    repo = _git_repo(tmp_path)
    job_dir = repo / "runs" / "job"
    arch.append_record(job_dir, arch.ArchiveRecord(
        id="0000", parents=[], cer=0.15, status="scored", hypothesis="h",
        what_i_learned="l", fingerprint=["t"], score_report=None, ts="t"))
    arch.write_best(job_dir, "0000")
    (job_dir / "0000").mkdir(parents=True, exist_ok=True)
    (job_dir / "0000" / "transcribe.py").write_text(
        "def transcribe(a, s):\n    return 'BEST'\n", encoding="utf-8")

    def boom(cfg_, best_id):
        raise RuntimeError("holdout exploded")
    monkeypatch.setattr(es, "_invoke_holdout", boom)

    cfg = es.SimpleConfig(job_id="job", repo_root=repo, holdout_every=1)
    archive = arch.load_archive(job_dir)
    try:
        es._maybe_holdout(cfg, iteration=0, archive=archive)
    except RuntimeError:
        pass
    # workspace restored to the committed stub despite the exception
    assert "return ''" in (repo / "workspace" / "transcribe.py").read_text(encoding="utf-8")


def test_maybe_holdout_returns_cer(tmp_path, monkeypatch):
    """Fix 3 plumbing: _maybe_holdout returns the holdout cer for the current
    best (None on dedup no-op)."""
    _seed_best(tmp_path)
    cfg = es.SimpleConfig(job_id="job", repo_root=tmp_path, holdout_every=1)
    monkeypatch.setattr(es, "_invoke_holdout", lambda cfg_, best_id: 0.16)
    archive = arch.load_archive(tmp_path / "runs" / "job")
    assert es._maybe_holdout(cfg, iteration=0, archive=archive) == 0.16
    # second call dedups -> returns None
    assert es._maybe_holdout(cfg, iteration=1, archive=archive) is None


def test_job_end_log_reports_in_loop_and_holdout_cer(tmp_path, monkeypatch):
    """Fix 3: the job-end summary log line reports in-loop best cer AND holdout
    cer together, and a job_end row carrying the holdout lands in <job>_log.jsonl."""
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
    monkeypatch.setattr(es, "_invoke_holdout", lambda cfg_, best_id: 0.21)
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

    cfg = es.SimpleConfig(job_id="job", repo_root=repo,
                          candidate_cmd=f"{sys.executable} {stub}",
                          iters=1, explore=0.0, parent_policy="best",
                          holdout_every=0)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        es.run_job(cfg)
    out = buf.getvalue()
    # one-line summary shows both numbers together
    assert "cer=0.1800" in out
    assert "hold=0.2100" in out
    # job_end row in the log jsonl carries the holdout cer
    log_rows = [json.loads(l) for l in
                (repo / "runs" / "_summary" / "job_log.jsonl")
                .read_text().splitlines() if l.strip()]
    job_end = [r for r in log_rows if r.get("event") == "job_end"]
    assert len(job_end) == 1
    assert job_end[0]["best_cer"] == 0.18
    assert job_end[0]["holdout"] == 0.21


def test_run_job_default_evaluates_holdout_only_at_job_end(tmp_path, monkeypatch):
    """holdout_every=0 (default): holdout is sealed during the search and
    evaluated exactly once, at job end, on the final best."""
    repo = _git_repo(tmp_path)
    stub = repo / "stub.py"
    stub.write_text(
        "import pathlib\n"
        "p = pathlib.Path('workspace/transcribe.py')\n"
        "p.write_text('def transcribe(a, s):\\n    return \\'hi\\'\\n')\n"
        "print('```yaml\\ncapability_investigated: a\\nwhat_i_learned: b\\n"
        "hypothesis: c\\nfingerprint: [t]\\n```')\n"
    )
    cers = iter([0.20, 0.18])

    class FakeVR:
        def __init__(self, cer):
            self.ok, self.report, self.error = True, {"corpus_cer": cer}, None
    monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR(next(cers)))
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")
    calls = []
    monkeypatch.setattr(es, "_invoke_holdout",
                        lambda cfg_, best_id: calls.append((cfg_.job_id, best_id)) or 0.17)

    cfg = es.SimpleConfig(job_id="job", repo_root=repo,
                          candidate_cmd=f"{sys.executable} {stub}",
                          iters=2, explore=0.0, parent_policy="best",
                          holdout_every=0)
    best_id = es.run_job(cfg)
    # exactly one job-end holdout on the FINAL best
    assert calls == [("job", best_id)]
    rows = [json.loads(l) for l in
            (repo / "runs" / "_summary" / "job_holdout.jsonl")
            .read_text().splitlines() if l.strip()]
    assert len(rows) == 1
    assert rows[0]["hyp_id"] == best_id


def test_run_job_leak_warning_only_when_holdout_every_positive(tmp_path, monkeypatch, capsys):
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
            self.ok, self.report, self.error = True, {"corpus_cer": 0.15}, None
    monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR())
    monkeypatch.setattr(es, "_invoke_holdout", lambda cfg_, best_id: 0.16)
    monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

    base = dict(job_id="job", repo_root=repo,
                candidate_cmd=f"{sys.executable} {stub}",
                iters=1, explore=0.0, parent_policy="best")

    es.run_job(es.SimpleConfig(holdout_every=0, **base))
    assert "WARNING" not in capsys.readouterr().out

    es.run_job(es.SimpleConfig(holdout_every=2, **base))
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "leak" in out.lower()
