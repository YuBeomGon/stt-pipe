from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import archive as arch
from harness import select_context as sc


def _rec(cid, cer, parents=None, fp=None):
    return arch.ArchiveRecord(
        id=cid, parents=parents or [], cer=cer, status="scored",
        hypothesis="h", what_i_learned="l", fingerprint=fp or [cid],
        score_report=None, ts="t",
    )


def _archive():
    return [_rec(f"{i:04d}", 0.30 - i * 0.01, fp=[f"f{i % 3}"]) for i in range(6)]


def test_policy_best_returns_min_cer():
    a = _archive()
    parent, _ = sc.select_context(a, K=8, recent=3, mode="EXPLOIT",
                                  policy="best", rng=random.Random(0))
    assert parent.id == arch.best_record(a).id


def test_policy_random_is_seed_deterministic():
    a = _archive()
    p1, _ = sc.select_context(a, K=8, recent=3, mode="EXPLORE",
                              policy="random", rng=random.Random(7))
    p2, _ = sc.select_context(a, K=8, recent=3, mode="EXPLORE",
                              policy="random", rng=random.Random(7))
    assert p1.id == p2.id


def test_policy_llm_exploit_uses_best():
    a = _archive()
    parent, _ = sc.select_context(a, K=8, recent=3, mode="EXPLOIT",
                                  policy="llm", rng=random.Random(0))
    assert parent.id == arch.best_record(a).id


def test_pinned_overrides_parent_in_exploit():
    a = _archive()
    parent, _ = sc.select_context(a, K=8, recent=3, mode="EXPLOIT",
                                  policy="best", rng=random.Random(0),
                                  pinned="0002")
    assert parent.id == "0002"


def test_inspirations_dedup_by_fingerprint_and_capped():
    a = _archive()
    _, inspr = sc.select_context(a, K=4, recent=2, mode="EXPLORE",
                                 policy="llm", rng=random.Random(1))
    seen_fp = [tuple(r.fingerprint) for r in inspr]
    assert len(seen_fp) == len(set(seen_fp))  # deduped
    assert len(inspr) <= 4


def test_empty_archive_returns_none_parent():
    parent, inspr = sc.select_context([], K=8, recent=3, mode="EXPLORE",
                                      policy="llm", rng=random.Random(0))
    assert parent is None
    assert inspr == []
