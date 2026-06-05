"""harness/evolve_simple.py — the radically simplified self-evolve loop.

never-prune flat archive + per-iter claude move + verify + keep-if-better.
No scheduler/lineage/portfolio/cooldown/signature/promotion/policy/gitops.
"""
from __future__ import annotations

import hashlib
import json
import random
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from harness import archive as arch
from harness import candidate_cli as cc
from harness import config as cfg
from harness import prompt_simple as ps
from harness import select_context as sc
from harness.verify import VerifyConfig, run_verify

# Package root (harness/.. = repo checkout that owns the static prompt + frozen
# surface assets). Static assets are package-owned, not per-job; only the
# workspace, runs/, and baseline live under the per-job repo_root.
_PKG_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class SimpleConfig:
    job_id: str
    repo_root: Path = Path(".")
    candidate_cmd: str = "claude -p"
    iters: int = 1
    explore: float = 0.5
    parent_policy: str = "llm"           # llm | random | best
    directive: str = ""
    bans: list[str] = field(default_factory=list)
    pinned: str | None = None
    holdout_every: int = 0
    recent: int = 5
    K: int = 8
    batch: str = "AIG_녹취반출_20250715"
    allowed_path: Path = Path("workspace/transcribe.py")
    baseline_file: Path = Path("baseline/target_cer.json")
    runtime_hard_multiplier: float = cfg.RUNTIME_HARD_MULTIPLIER

    @property
    def job_dir(self) -> Path:
        return self.repo_root / "runs" / self.job_id

    @property
    def summary_dir(self) -> Path:
        return self.repo_root / "runs" / "_summary"


def _seed_for(job_id: str, iteration: int) -> int:
    h = hashlib.sha256(f"{job_id}:{iteration}".encode()).hexdigest()
    return int(h[:16], 16)


def _coin(job_id: str, iteration: int) -> float:
    """Deterministic, resume-safe coin in [0, 1) from (job, iter)."""
    return (_seed_for(job_id, iteration) % 1_000_000) / 1_000_000.0


def _run_git(repo_root: Path, args: list[str], check: bool = True):
    return subprocess.run(["git", *args], cwd=repo_root, check=check,
                          capture_output=True, text=True)


def _out_of_scope(repo_root: Path, allowed_path: Path) -> set[str]:
    """Set of dirty paths that are NOT workspace/transcribe.py and NOT under
    runs/. (runs/ is gitignored so it won't appear in plain porcelain anyway;
    the prefix check is defensive.)"""
    res = _run_git(repo_root, ["status", "--porcelain", "--untracked-files=all"],
                   check=False)
    allowed = allowed_path.as_posix()
    out: set[str] = set()
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:]
        if " -> " in path:                       # rename: take the destination
            path = path.split(" -> ", 1)[1]
        if path == allowed or path.startswith("runs/"):
            continue
        out.add(path)
    return out


def _scope_ok(before: set[str], after: set[str]) -> bool:
    """DELTA scope check: reject only if the candidate introduced NEW
    out-of-scope changes (after - before is non-empty).

    Absolute "tree must be clean except workspace" is WRONG here: the operator
    repo is normally dirty (untracked docs, proposals, plans, .claude.alt/...),
    which would falsely reject EVERY candidate. We snapshot the out-of-scope
    set right before invoking the candidate and compare after — so pre-existing
    untracked files are ignored and only the candidate's own out-of-scope
    writes trip the gate."""
    return not (after - before)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_asset(repo_root: Path, rel: Path) -> str:
    """Read a package-owned static asset (prompt profile, frozen surface).

    Prefer the per-job repo_root copy, fall back to the package checkout so
    test repos that don't vendor the static assets still resolve them."""
    candidate = repo_root / rel
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return (_PKG_ROOT / rel).read_text(encoding="utf-8")


def _write_state(cfg_: SimpleConfig, archive: list[arch.ArchiveRecord]) -> None:
    best = arch.best_record(archive)
    state = {
        "job_id": cfg_.job_id,
        "iterations": len(archive),
        "best_hyp_id": best.id if best else None,
        "best_cer": best.cer if best else None,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    cfg_.summary_dir.mkdir(parents=True, exist_ok=True)
    (cfg_.summary_dir / f"{cfg_.job_id}_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_log(cfg_: SimpleConfig, line: dict) -> None:
    cfg_.summary_dir.mkdir(parents=True, exist_ok=True)
    with (cfg_.summary_dir / f"{cfg_.job_id}_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def run_iter(
    cfg_: SimpleConfig, iteration: int, archive: list[arch.ArchiveRecord]
) -> arch.ArchiveRecord:
    repo = cfg_.repo_root
    cid = arch.next_id(archive)
    out_dir = cfg_.job_dir / cid
    mode = "EXPLORE" if _coin(cfg_.job_id, iteration) < cfg_.explore else "EXPLOIT"
    rng = random.Random(_seed_for(cfg_.job_id, iteration))

    parent, inspr = sc.select_context(
        archive, K=cfg_.K, recent=cfg_.recent, mode=mode,
        policy=cfg_.parent_policy, rng=rng, pinned=cfg_.pinned,
    )

    # Materialize the chosen parent (if any) so the LLM edits real code.
    if parent is not None:
        arch.materialize_parent(cfg_.job_dir, parent.id, repo / cfg_.allowed_path)

    baseline = _read_json(repo / cfg_.baseline_file)
    profile = _read_asset(repo, Path("harness") / "prompts" / "candidate_simple.md")
    frozen_surface = _read_asset(repo, Path("frozen") / "asr_backend.py")
    workspace_body = (repo / cfg_.allowed_path).read_text(encoding="utf-8")
    best = arch.best_record(archive)

    prompt = ps.build_simple_prompt(
        profile=profile, frozen_surface=frozen_surface, workspace_body=workspace_body,
        baseline=baseline, mode=mode, directive=cfg_.directive, bans=cfg_.bans,
        parent=parent, inspirations=inspr, allowed_path=cfg_.allowed_path.as_posix(),
        best_cer=best.cer if best else None, best_hyp_id=best.id if best else None,
        archive=archive,
    )

    # Snapshot out-of-scope dirty set BEFORE the candidate runs, so the
    # delta scope check below ignores pre-existing untracked files.
    before_scope = _out_of_scope(repo, cfg_.allowed_path)

    result = cc.run_candidate_command(
        candidate_cmd=cfg_.candidate_cmd, prompt=prompt, out_dir=out_dir,
        repo_root=repo, workspace_file=cfg_.allowed_path,
    )

    ts = datetime.now(UTC).isoformat()
    meta, reason = cc.parse_candidate_metadata(result.stdout, out_dir=out_dir)

    def _finalize(status: str, cer, score_report, m: dict | None):
        rec = arch.ArchiveRecord(
            id=cid, parents=[parent.id] if parent else [], cer=cer, status=status,
            hypothesis=(m or {}).get("hypothesis", ""),
            what_i_learned=(m or {}).get("what_i_learned", ""),
            fingerprint=(m or {}).get("fingerprint", []),
            score_report=score_report, ts=ts, mode=mode,
            lane=(m or {}).get("lane"),
            capability_investigated=(m or {}).get("capability_investigated", ""),
        )
        arch.append_record(cfg_.job_dir, rec)
        return rec

    # command failed
    if result.returncode != 0:
        rec = _finalize("command_failed", None, None, meta)
        _restore(repo, cfg_.allowed_path)
        _emit_log(cfg_, iteration, mode, parent, rec, kept=False, holdout=None)
        archive.append(rec)
        _write_state(cfg_, archive)
        return rec

    # format reject
    if meta is None:
        rec = _finalize("format_reject", None, None, None)
        _restore(repo, cfg_.allowed_path)
        _emit_log(cfg_, iteration, mode, parent, rec, kept=False, holdout=None)
        archive.append(rec)
        _write_state(cfg_, archive)
        return rec

    # scope reject — candidate introduced a NEW out-of-scope change
    if not _scope_ok(before_scope, _out_of_scope(repo, cfg_.allowed_path)):
        rec = _finalize("scope_reject", None, None, meta)
        _restore(repo, cfg_.allowed_path)
        _emit_log(cfg_, iteration, mode, parent, rec, kept=False, holdout=None)
        archive.append(rec)
        _write_state(cfg_, archive)
        return rec

    # snapshot the evaluated code BEFORE verify/restore so it survives as a parent
    arch.snapshot_candidate(cfg_.job_dir, cid, repo / cfg_.allowed_path)

    vr = run_verify(VerifyConfig(
        repo_root=repo, hyp_id=f"{cfg_.job_id}/{cid}", batch=cfg_.batch,
        workspace_file=cfg_.allowed_path, baseline_file=cfg_.baseline_file,
        runs_dir=Path("runs"), runtime_hard_multiplier=cfg_.runtime_hard_multiplier,
    ))
    if not vr.ok:
        rec = _finalize("rejected", None, None, meta)
        _restore(repo, cfg_.allowed_path)
        _emit_log(cfg_, iteration, mode, parent, rec, kept=False, holdout=None)
        archive.append(rec)
        _write_state(cfg_, archive)
        return rec

    cer = float(vr.report["corpus_cer"])
    rec = _finalize("scored", cer, f"{cid}/score_report.json", meta)
    archive.append(rec)

    # keep-if-better: advance best only on a real improvement
    prior_best = arch.best_record(archive[:-1])
    kept = prior_best is None or cer < prior_best.cer - cfg.KEEP_DELTA_EPS
    if kept:
        arch.write_best(cfg_.job_dir, cid)

    _restore(repo, cfg_.allowed_path)
    _write_state(cfg_, archive)
    _emit_log(cfg_, iteration, mode, parent, rec, kept=kept, holdout=None)
    return rec


def _targets_claude(candidate_cmd: str) -> bool:
    """True when the candidate CLI is actually `claude` (basename of argv[0]).

    The hardening bypass only matters for a real `claude -p` invocation —
    `harden_candidate_cmd` is a no-op for any other command, so the production
    bypass gate is spurious for stub/non-claude candidate commands."""
    parts = shlex.split(candidate_cmd)
    return bool(parts) and Path(parts[0]).name == "claude"


def run_job(cfg_: SimpleConfig) -> str | None:
    """Run exactly cfg_.iters iterations. Returns the best id (or None).

    Holdout policy (operator decision — do not weaken):
      * Default (holdout_every == 0): the holdout stays SEALED during the
        search and is evaluated exactly ONCE, at job end, on the final best.
      * holdout_every == K > 0 (opt-in, measurement/ablation only): ADDITIONALLY
        peek at the holdout every K iterations on the current best. This risks
        leakage — an operator reacting to a mid-run holdout number is indirect
        selection — so we emit a one-line WARNING at job start.
    In all cases the holdout cer is REPORTING ONLY: it is written to the
    <job>_holdout.jsonl sidecar and never advances best.txt or feeds parent
    selection (selection uses in-loop cer exclusively)."""
    if _targets_claude(cfg_.candidate_cmd):
        cc.check_bypass_in_production(cfg_.iters, commit_results=False)
    if cfg_.holdout_every and cfg_.holdout_every > 0:
        print(
            f"WARNING: --holdout-every {cfg_.holdout_every} peeks at the SEALED "
            "holdout mid-run; reacting to those numbers leaks the holdout into "
            "selection. Recorded for reporting only — do not steer on it."
        )
    archive = arch.load_archive(cfg_.job_dir)
    start = len(archive)
    for i in range(cfg_.iters):
        iteration = start + i
        run_iter(cfg_, iteration=iteration, archive=archive)
        # Mid-run peeking is opt-in (K>0) and throttled to every Kth iteration;
        # _maybe_holdout dedups per best id so unchanged-best iters are no-ops.
        if cfg_.holdout_every and cfg_.holdout_every > 0 \
                and (iteration + 1) % cfg_.holdout_every == 0:
            _maybe_holdout(cfg_, iteration=iteration, archive=archive)
    # Default behavior: always evaluate the holdout once at job end on the final
    # best (dedup makes this a no-op if a K>0 peek already covered this best id).
    _maybe_holdout(cfg_, iteration=start + cfg_.iters - 1, archive=archive)
    best = arch.best_record(archive)
    return best.id if best else None


def _invoke_holdout(cfg_: SimpleConfig, best_id: str) -> float | None:
    """Run the existing holdout script anchored on this job's state and return
    the holdout corpus_cer.

    REPORTING-ONLY. The return value is recorded to the sidecar/state for the
    leaderboard and NEVER feeds keep-if-better or parent selection.

    Anchoring contract (the only coupling): scripts/evaluate_holdout.py resolves
    the 0715 eval run via runs/_summary/<job>_state.json::best_hyp_id — which
    _write_state already keeps current — and requires a JOB_DONE.lock under
    --summary-dir, which we create for the duration. Best-effort: a failed
    holdout returns None and never crashes the loop. Tests MUST monkeypatch this
    so the real sealed corpus is never touched.
    """
    lock = cfg_.summary_dir / "JOB_DONE.lock"
    created = False
    if not lock.is_file():
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("simple-evolve holdout cadence\n", encoding="utf-8")
        created = True
    try:
        subprocess.run(
            [sys.executable, "-m", "scripts.evaluate_holdout",
             "--unseal", "--job-id", cfg_.job_id,
             "--summary-dir", str(cfg_.summary_dir),
             "--runs-dir", str(cfg_.repo_root / "runs")],
            cwd=cfg_.repo_root, check=False,
        )
    except OSError:
        return None
    finally:
        if created and lock.is_file():
            lock.unlink()
    # evaluate_holdout writes docs/reports/<job>_HOLDOUT_<date>.json with the
    # holdout corpus_cer under key "holdout_cer"; read the newest one for this job.
    reports = sorted(
        (cfg_.repo_root / "docs" / "reports").glob(f"{cfg_.job_id}_HOLDOUT_*.json")
    )
    if not reports:
        return None
    return _read_json(reports[-1]).get("holdout_cer")


def _maybe_holdout(cfg_: SimpleConfig, iteration: int, archive) -> None:
    """Evaluate the holdout on the CURRENT best and record it to the
    <job>_holdout.jsonl sidecar (read by scripts/archive_summary.py).

    Each best id is evaluated at most once (dedup), so repeated calls while best
    is unchanged are cheap no-ops. Holdout cer is recorded for REPORTING ONLY —
    nothing here advances best.txt or feeds selection.
    """
    best = arch.best_record(archive)
    if best is None:
        return
    sidecar = cfg_.summary_dir / f"{cfg_.job_id}_holdout.jsonl"
    already: set[str | None] = set()
    if sidecar.is_file():
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    already.add(json.loads(line).get("hyp_id"))
                except json.JSONDecodeError:
                    pass
    if best.id in already:
        return  # only evaluate each new best once
    holdout_cer = _invoke_holdout(cfg_, best.id)
    cfg_.summary_dir.mkdir(parents=True, exist_ok=True)
    with sidecar.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "iter": iteration, "hyp_id": best.id,
            "in_loop_cer": best.cer, "holdout_cer": holdout_cer,
        }, ensure_ascii=False) + "\n")


def _restore(repo_root: Path, allowed_path: Path) -> None:
    _run_git(repo_root, ["restore", "--", allowed_path.as_posix()], check=False)


def _emit_log(cfg_, iteration, mode, parent, rec, kept, holdout):
    pid = parent.id if parent else "—"
    pcer = f"{parent.cer:.4f}" if parent and parent.cer is not None else "n/a"
    cer_str = f"{rec.cer:.4f}" if rec.cer is not None else rec.status
    decision = "KEEP" if kept else "—"
    hold_str = f" | hold={holdout:.4f}" if holdout is not None else ""
    line = (
        f"iter {iteration:03d} | {mode} | parent={pid}({pcer}) | "
        f"id={rec.id} | cer={cer_str} | {decision}{hold_str}"
    )
    print(line)
    _write_log(cfg_, {
        "iter": iteration, "mode": mode, "parent": pid, "id": rec.id,
        "cer": rec.cer, "status": rec.status, "kept": kept,
        "holdout": holdout, "fingerprint": rec.fingerprint,
    })
