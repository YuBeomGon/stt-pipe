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
import time
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

# Indirection so tests can monkeypatch (`es._sleep`) and never actually sleep
# during the rate-limit backoff ladder.
_sleep = time.sleep


class RateLimitAbort(Exception):
    """Raised by run_iter when the session/token rate-limit backoff ladder
    (cc.RATE_LIMIT_BACKOFF_MIN) is exhausted and the candidate output is STILL
    rate-limited. This is a wait/abort condition, not a candidate failure — no
    archive row is appended for the aborted iter. run_job catches it, records
    job status ``aborted_rate_limit`` and stops cleanly; re-running with the
    same --job-id resumes (archive/state persist)."""


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


def _err_tail(error: str | None, stderr: str, limit: int = 1200) -> str:
    """Compact failure reason = the error message + the last `limit` chars of
    stderr (stripped, whitespace collapsed). This is the signal fed back to the
    next candidate so it can see WHY the last attempt failed."""
    parts: list[str] = []
    err = (error or "").strip()
    if err:
        parts.append(err)
    tail = (stderr or "").strip()
    if tail:
        if len(tail) > limit:
            tail = tail[-limit:]
        parts.append(tail)
    return " ".join(" ".join(parts).split())


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


def _write_state(cfg_: SimpleConfig, archive: list[arch.ArchiveRecord],
                 status: str = "completed") -> None:
    best = arch.best_record(archive)
    state = {
        "job_id": cfg_.job_id,
        "status": status,
        "iterations": len(archive),
        # NOTE: the verify report for candidate <cid> lives at
        # runs/<job_id>/<cid>/score_report.json (run_verify is called with
        # hyp_id=f"{job_id}/{cid}"). scripts/evaluate_holdout._best_eval_run
        # resolves the anchor as runs/<best_hyp_id>/score_report.json, so the
        # state field MUST carry the job-qualified id, not the bare cid.
        "best_hyp_id": f"{cfg_.job_id}/{best.id}" if best else None,
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


def _candidate_text(out_dir: Path, result) -> str:
    """Combined candidate stdout+stderr used for rate-limit detection. Prefers
    the saved claude_stdout.txt/claude_stderr.txt (matching how the old runner
    sourced the text) and falls back to the CompletedProcess attrs."""
    parts: list[str] = []
    for fname, attr in (("claude_stdout.txt", "stdout"),
                        ("claude_stderr.txt", "stderr")):
        p = out_dir / fname
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8"))
        else:
            val = getattr(result, attr, None)
            if val:
                parts.append(val)
    return "\n".join(parts)


def _run_candidate_with_backoff(cfg_: SimpleConfig, prompt: str, out_dir: Path,
                                iteration: int):
    """Run the candidate once; if the output looks rate-limited, retry on the
    cc.RATE_LIMIT_BACKOFF_MIN ladder (5·10·20·40·80·80 min). Returns the
    CompletedProcess from the first non-rate-limited attempt. Raises
    RateLimitAbort if the ladder is exhausted and STILL rate-limited."""
    def _invoke():
        return cc.run_candidate_command(
            candidate_cmd=cfg_.candidate_cmd, prompt=prompt, out_dir=out_dir,
            repo_root=cfg_.repo_root, workspace_file=cfg_.allowed_path,
        )

    result = _invoke()
    for wait_min in cc.RATE_LIMIT_BACKOFF_MIN:
        if not cc.is_rate_limited(_candidate_text(out_dir, result)):
            return result
        print(
            f"[rate-limit] 세션/토큰 한도 감지 — {wait_min}분 후 재시도 "
            f"(iter {iteration})",
            flush=True,
        )
        _sleep(wait_min * 60)
        result = _invoke()
    if cc.is_rate_limited(_candidate_text(out_dir, result)):
        raise RateLimitAbort(
            f"rate limit persisted through backoff ladder at iter {iteration}"
        )
    return result


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

    # Session/token rate limit is transient: retry the SAME iter on a backoff
    # ladder (5·10·20·40·80·80 min) instead of burning it as an instant reject.
    # If the ladder is exhausted and still limited, this raises RateLimitAbort,
    # which run_job catches to stop cleanly (no bogus archive row for this iter).
    result = _run_candidate_with_backoff(cfg_, prompt, out_dir, iteration)

    ts = datetime.now(UTC).isoformat()
    meta, reason = cc.parse_candidate_metadata(result.stdout, out_dir=out_dir)

    def _finalize(status: str, cer, score_report, m: dict | None,
                  error: str | None = None):
        rec = arch.ArchiveRecord(
            id=cid, parents=[parent.id] if parent else [], cer=cer, status=status,
            hypothesis=(m or {}).get("hypothesis", ""),
            what_i_learned=(m or {}).get("what_i_learned", ""),
            fingerprint=(m or {}).get("fingerprint", []),
            score_report=score_report, ts=ts, mode=mode,
            lane=(m or {}).get("lane"),
            capability_investigated=(m or {}).get("capability_investigated", ""),
            error=error,
        )
        arch.append_record(cfg_.job_dir, rec)
        return rec

    def _write_verify_error(text: str) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "verify_error.txt").write_text(text or "", encoding="utf-8")

    # command failed
    if result.returncode != 0:
        cmd_err = _err_tail(None, result.stderr or result.stdout)
        rec = _finalize("command_failed", None, None, meta, error=cmd_err)
        _write_verify_error(cmd_err)
        _restore(repo, cfg_.allowed_path)
        _emit_log(cfg_, iteration, mode, parent, rec, kept=False)
        archive.append(rec)
        _write_state(cfg_, archive)
        return rec

    # format reject
    if meta is None:
        rec = _finalize("format_reject", None, None, None, error=reason)
        _restore(repo, cfg_.allowed_path)
        _emit_log(cfg_, iteration, mode, parent, rec, kept=False)
        archive.append(rec)
        _write_state(cfg_, archive)
        return rec

    # scope reject — candidate introduced a NEW out-of-scope change
    after_scope = _out_of_scope(repo, cfg_.allowed_path)
    if not _scope_ok(before_scope, after_scope):
        new_paths = sorted(after_scope - before_scope)
        scope_err = "out-of-scope writes: " + ", ".join(new_paths)
        rec = _finalize("scope_reject", None, None, meta, error=scope_err)
        _restore(repo, cfg_.allowed_path)
        _emit_log(cfg_, iteration, mode, parent, rec, kept=False)
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
        verify_err = _err_tail(getattr(vr, "error", None), getattr(vr, "stderr", ""))
        rec = _finalize("rejected", None, None, meta, error=verify_err)
        _write_verify_error(verify_err)
        _restore(repo, cfg_.allowed_path)
        _emit_log(cfg_, iteration, mode, parent, rec, kept=False)
        archive.append(rec)
        _write_state(cfg_, archive)
        return rec

    cer = float(vr.report["corpus_cer"])
    rec = _finalize("scored", cer, f"{cid}/score_report.json", meta, error=None)
    archive.append(rec)

    # keep-if-better: advance best only on a real improvement
    prior_best = arch.best_record(archive[:-1])
    kept = prior_best is None or cer < prior_best.cer - cfg.KEEP_DELTA_EPS
    if kept:
        arch.write_best(cfg_.job_dir, cid)

    _restore(repo, cfg_.allowed_path)
    _write_state(cfg_, archive)
    _emit_log(cfg_, iteration, mode, parent, rec, kept=kept)
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

    Holdout policy (operator decision): the loop NEVER touches the sealed
    holdout. It computes the in-loop CER only and records the best id in
    runs/_summary/<job>_state.json::best_hyp_id. After a run, the operator
    validates the best on the sealed holdout MANUALLY:

        python scripts/evaluate_holdout.py --unseal --job-id <id>

    Auto holdout invocation was removed because the auto re-seal
    (`chmod -R 000`) is unreliable when the harness runs as the candidate user
    (re-seal failed with "Permission denied" in a live run), risking an
    unsealed holdout. Manual operator invocation keeps the seal under operator
    control."""
    if _targets_claude(cfg_.candidate_cmd):
        cc.check_bypass_in_production(cfg_.iters, commit_results=False)
    archive = arch.load_archive(cfg_.job_dir)
    start = len(archive)
    iteration = start
    for i in range(cfg_.iters):
        iteration = start + i
        try:
            run_iter(cfg_, iteration=iteration, archive=archive)
        except RateLimitAbort:
            # Backoff ladder exhausted and still rate-limited. No archive row
            # was appended for this iter — stop cleanly, persist state with
            # status=aborted_rate_limit, and let the operator resume later with
            # the same --job-id (load_archive picks up prior rows; next_id
            # continues, evaluated budget is preserved).
            best = arch.best_record(archive)
            _write_state(cfg_, archive, status="aborted_rate_limit")
            best_id = best.id if best else "—"
            best_cer = (f"{best.cer:.4f}"
                        if best and best.cer is not None else "n/a")
            print(
                f"job {cfg_.job_id} | ABORTED (rate limit) — best so far="
                f"{best_id} cer={best_cer}; resume with the same --job-id later"
            )
            return best.id if best else None
    job_end_iter = start + cfg_.iters - 1
    best = arch.best_record(archive)
    _emit_job_summary(cfg_, iteration=job_end_iter, best=best)
    return best.id if best else None


def _emit_job_summary(cfg_: SimpleConfig, iteration: int, best) -> None:
    """Emit a one-line job-end summary reporting the final in-loop best cer, plus
    a hint to run the holdout MANUALLY. The loop never touches the holdout."""
    if best is None:
        line = f"job {cfg_.job_id} | DONE | no scored candidate"
        print(line)
        _write_log(cfg_, {
            "iter": iteration, "event": "job_end", "best_id": None,
            "best_cer": None,
        })
        return
    best_str = f"{best.cer:.4f}" if best.cer is not None else "n/a"
    print(f"job {cfg_.job_id} | DONE | best={best.id} | cer={best_str}")
    print(
        f"# holdout: run `python scripts/evaluate_holdout.py --unseal "
        f"--job-id {cfg_.job_id}` manually to validate on the sealed set"
    )
    _write_log(cfg_, {
        "iter": iteration, "event": "job_end", "best_id": best.id,
        "best_cer": best.cer,
    })


def _restore(repo_root: Path, allowed_path: Path) -> None:
    _run_git(repo_root, ["restore", "--", allowed_path.as_posix()], check=False)


def _emit_log(cfg_, iteration, mode, parent, rec, kept):
    pid = parent.id if parent else "—"
    pcer = f"{parent.cer:.4f}" if parent and parent.cer is not None else "n/a"
    if rec.cer is not None:
        cer_str = f"{rec.cer:.4f}"
    else:
        # Non-scored row: surface WHY it failed, not a bare status. Truncate the
        # reason to one short line so the log stays scannable.
        reason = " ".join((rec.error or "").split())
        if len(reason) > 80:
            reason = reason[:80] + "…"
        cer_str = f"{rec.status}: {reason}" if reason else rec.status
    decision = "KEEP" if kept else "—"
    line = (
        f"iter {iteration:03d} | {mode} | parent={pid}({pcer}) | "
        f"id={rec.id} | cer={cer_str} | {decision}"
    )
    print(line)
    _write_log(cfg_, {
        "iter": iteration, "mode": mode, "parent": pid, "id": rec.id,
        "cer": rec.cer, "status": rec.status, "kept": kept,
        "fingerprint": rec.fingerprint,
    })
