"""tests/test_promotion.py — serialized gated promotion (phase3). No audio data."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from harness import gitops, promotion

pytestmark = pytest.mark.promotion


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "workspace").mkdir()
    (root / "workspace" / "transcribe.py").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c1")
    gitops.ensure_champion_ref(root, "champion")
    return root


def _baseline() -> dict:
    # target_cer high enough that "success" never triggers → tests exercise keep/reject.
    return {"target_cer": 0.0, "total_inference_time_s": 100.0}


def _rep(cer: float) -> dict:
    return {"corpus_cer": cer, "total_inference_time_s": 90.0}


def _commit_candidate(repo: Path, tmp_path: Path, name: str, body: str) -> str:
    wt = tmp_path / f"wt-{name}"
    gitops.prepare_job_worktree(repo, wt, f"job/{name}", "champion")
    (wt / "workspace" / "transcribe.py").write_text(body, encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", f"cand {name}")
    return _git(wt, "rev-parse", "HEAD")


def test_clean_promote_advances_champion_and_records_map(repo: Path, tmp_path) -> None:
    src = _commit_candidate(repo, tmp_path, "j", "WIN\n")
    res = promotion.try_promote(
        repo, job_id="j", source_commit=src,
        rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(0.12),
        baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
    assert res.status == promotion.PROMOTE
    assert gitops.read_ref(repo, "refs/heads/champion") == res.champion_commit
    rows = [l for l in (repo / "runs/_summary/promotion_map.jsonl").read_text().splitlines() if l.strip()]
    assert json.loads(rows[-1])["job_id"] == "j"
    assert _git(repo, "show", "champion:workspace/transcribe.py") == "WIN"


def test_lost_race_when_live_champion_already_lower(repo: Path, tmp_path) -> None:
    # pre-seed a live champion at 0.10; our 0.12 candidate must LOSE re-validation.
    mp = repo / "runs/_summary/promotion_map.jsonl"
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"job_id": "x", "cer": 0.10, "champion_commit": "deadbeef"}) + "\n",
                  encoding="utf-8")
    before = gitops.read_ref(repo, "refs/heads/champion")
    src = _commit_candidate(repo, tmp_path, "j", "LATE\n")
    res = promotion.try_promote(
        repo, job_id="j", source_commit=src,
        rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(0.12),
        baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
    assert res.status == promotion.LOST
    assert gitops.read_ref(repo, "refs/heads/champion") == before   # unmoved


def test_seed_makes_first_real_promote_compare_against_baseline(repo: Path, tmp_path) -> None:
    # seed the bootstrap champion CER at 0.15; a 0.16 candidate must NOT beat it
    # (without the seed, an empty map would let it auto-win — the I3 defect).
    promotion.seed_champion_cer(repo, Path("runs/_summary"), baseline_cer=0.15)
    src = _commit_candidate(repo, tmp_path, "j", "WORSE\n")
    res = promotion.try_promote(
        repo, job_id="j", source_commit=src,
        rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(0.16),
        baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
    assert res.status == promotion.LOST
    # idempotent: seeding again does not add a second bootstrap row.
    promotion.seed_champion_cer(repo, Path("runs/_summary"), baseline_cer=0.99)
    rows = [l for l in (repo / "runs/_summary/promotion_map.jsonl").read_text().splitlines() if l.strip()]
    assert sum(1 for r in rows if json.loads(r)["job_id"] == "__bootstrap__") == 1


def test_lock_serializes_concurrent_promoters(repo: Path, tmp_path) -> None:
    # two threads, each promoting a distinct improving candidate; assert every map
    # line is valid JSON (no torn write) and at least one promotes.
    src_a = _commit_candidate(repo, tmp_path, "a", "AAA\n")
    src_b = _commit_candidate(repo, tmp_path, "b", "BBB\n")
    results: list[promotion.PromotionResult] = []
    lock = threading.Lock()

    def go(jid: str, cer: float, src: str) -> None:
        r = promotion.try_promote(
            repo, job_id=jid, source_commit=src,
            rel_path=Path("workspace/transcribe.py"), candidate_report=_rep(cer),
            baseline=_baseline(), sigma=0.0, sigma_is_provisional=True)
        with lock:
            results.append(r)

    ts = [threading.Thread(target=go, args=a) for a in (("a", 0.13, src_a), ("b", 0.11, src_b))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rows = [l for l in (repo / "runs/_summary/promotion_map.jsonl").read_text().splitlines() if l.strip()]
    assert all(json.loads(r) for r in rows)             # every line parses → no tear
    assert promotion.PROMOTE in {r.status for r in results}
