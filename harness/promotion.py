"""harness/promotion.py
Serialized, gated promotion to the protected champion (HARNESS-REDESIGN §52/§82).

Only one promoter at a time (fcntl.flock, auto-released on death). Under the lock
we RE-VALIDATE against the LIVE champion CER (a peer job may have promoted lower
while we queued — the A-vs-B race), then CAS-advance champion splicing only
transcribe.py. A lost race returns LOST; the runner then re-decides the set step
with beats_champion=False so the candidate is kept as the lineage head (NOT
discarded). NOT GitHub branch protection — the operator is solo + local, so the
file lock is the real mechanism.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from harness import gitops
from harness.policy import PolicyConfig, decide_promotion

PROMOTE = "promoted"
LOST = "lost_race"


@dataclass(frozen=True)
class PromotionResult:
    status: str                 # "promoted" | "lost_race"
    champion_commit: str | None
    live_champion_cer: float | None
    reason: str


def _lock_path(repo_root: Path) -> Path:
    return repo_root / ".git" / "champion_promote.lock"


def _map_path(repo_root: Path, summary_dir: Path) -> Path:
    return repo_root / summary_dir / "promotion_map.jsonl"


def seed_champion_cer(repo_root: Path, summary_dir: Path, baseline_cer: float,
                      champion_commit: str | None = None) -> None:
    """I3 fix: write a bootstrap row into promotion_map.jsonl so live_champion_cer
    never returns None (which decide_promotion treats as "first candidate always
    wins"). Idempotent: a no-op if the map already has any row. ``baseline_cer`` is
    the measured CER of the bootstrap champion (baseline/target_cer.json:baseline_cer)."""
    p = _map_path(repo_root, summary_dir)
    if p.is_file() and any(line.strip() for line in p.read_text(encoding="utf-8").splitlines()):
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"job_id": "__bootstrap__", "source_commit": champion_commit,
           "champion_commit": champion_commit, "cer": float(baseline_cer),
           "ts": time.time()}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def live_champion_cer(repo_root: Path, summary_dir: Path) -> float | None:
    """Lowest CER recorded so far (promotion_map.jsonl, incl. the bootstrap seed
    row). None only if the map is genuinely empty AND was never seeded — in normal
    operation seed_champion_cer guarantees at least one row."""
    p = _map_path(repo_root, summary_dir)
    if not p.is_file():
        return None
    best: float | None = None
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cer = json.loads(line).get("cer")
        if isinstance(cer, (int, float)) and (best is None or cer < best):
            best = cer
    return best


def try_promote(
    repo_root: Path, *, job_id: str, source_commit: str, rel_path: Path,
    candidate_report: dict, baseline: dict, sigma: float | None,
    sigma_is_provisional: bool, champion_ref: str = "champion",
    summary_dir: Path = Path("runs/_summary"),
    absolute_delta_fallback: float | None = None,
) -> PromotionResult:
    """Acquire the promotion lock, re-validate vs the LIVE champion, CAS-advance,
    record the map. ``repo_root`` is the SHARED main repo (where champion lives),
    NOT the job worktree."""
    lock = _lock_path(repo_root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)             # serialize; auto-released on death
        live = live_champion_cer(repo_root, summary_dir)
        pcfg = (PolicyConfig(absolute_delta_fallback=absolute_delta_fallback)
                if absolute_delta_fallback is not None else None)
        revalidate = decide_promotion(
            report=candidate_report, baseline=baseline, champion_cer=live,
            sigma=sigma, sigma_is_provisional=sigma_is_provisional, config=pcfg,
        )
        if revalidate.status not in ("keep", "success"):
            return PromotionResult(LOST, None, live,
                                   f"lost race: live champion {live} not beaten")
        expected_old = gitops.read_ref(repo_root, f"refs/heads/{champion_ref}")
        new = gitops.promote_to_champion(
            repo_root, champion_ref, source_commit, rel_path,
            expected_old=expected_old,
            message=f"promote {job_id} {source_commit[:8]} cer={revalidate.candidate_cer:.6f}",
        )
        if new is None:
            return PromotionResult(LOST, None, live, "lost race: champion CAS failed")
        rec = {"job_id": job_id, "source_commit": source_commit,
               "champion_commit": new, "cer": revalidate.candidate_cer,
               "ts": time.time()}
        mp = _map_path(repo_root, summary_dir)
        mp.parent.mkdir(parents=True, exist_ok=True)
        with mp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return PromotionResult(PROMOTE, new, revalidate.candidate_cer, "promoted")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
