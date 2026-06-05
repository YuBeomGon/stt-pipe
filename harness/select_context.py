"""harness/select_context.py — parent + inspiration slate from the flat archive.

Diversity/exploration is an emergent property of stochastic sampling over the
never-pruned archive, NOT a scheduler. Three parent policies for the ablation:
  best   — always the global best (pure hill-climb control)
  random — seeded RNG over scored records (control)
  llm    — weighted_random: Boltzmann perf-weight / (1 + children) (treatment).
           EXPLOIT mode anchors on best; EXPLORE mode samples by weight.
"""
from __future__ import annotations

import math
import random

from harness import archive as arch
from harness.archive import ArchiveRecord

_T = 0.05  # Boltzmann temperature (internal constant, not an operator knob)


def _scored(archive: list[ArchiveRecord]) -> list[ArchiveRecord]:
    return [r for r in archive if r.status == "scored" and r.cer is not None]


def _norm_scores(scored: list[ArchiveRecord]) -> dict[str, float]:
    cers = [r.cer for r in scored]
    lo, hi = min(cers), max(cers)
    span = (hi - lo) or 1.0
    # lower cer -> higher score in [0, 1]
    return {r.id: (hi - r.cer) / span for r in scored}


def _weighted_random(
    archive: list[ArchiveRecord], rng: random.Random
) -> ArchiveRecord | None:
    scored = _scored(archive)
    if not scored:
        return None
    norm = _norm_scores(scored)
    weights = [
        math.exp(norm[r.id] / _T) / (1 + arch.children_count(archive, r.id))
        for r in scored
    ]
    return rng.choices(scored, weights=weights, k=1)[0]


def top_by_cer(archive: list[ArchiveRecord], n: int) -> list[ArchiveRecord]:
    return sorted(_scored(archive), key=lambda r: r.cer)[:n]


def diverse_sample(
    archive: list[ArchiveRecord], n: int, exclude_fp: set[tuple[str, ...]],
    rng: random.Random,
) -> list[ArchiveRecord]:
    pool = [r for r in _scored(archive) if tuple(r.fingerprint) not in exclude_fp]
    rng.shuffle(pool)
    out: list[ArchiveRecord] = []
    seen = set(exclude_fp)
    for r in pool:
        fp = tuple(r.fingerprint)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(r)
        if len(out) >= n:
            break
    return out


def recent_attempts(archive: list[ArchiveRecord], n: int) -> list[ArchiveRecord]:
    return archive[-n:] if n > 0 else []


def dedup_by_fingerprint(records: list[ArchiveRecord]) -> list[ArchiveRecord]:
    seen: set[tuple[str, ...]] = set()
    out: list[ArchiveRecord] = []
    for r in records:
        fp = tuple(r.fingerprint)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(r)
    return out


def select_context(
    archive: list[ArchiveRecord],
    K: int,
    recent: int,
    mode: str,                 # "EXPLORE" | "EXPLOIT"
    policy: str,               # "llm" | "random" | "best"
    rng: random.Random,
    pinned: str | None = None,
) -> tuple[ArchiveRecord | None, list[ArchiveRecord]]:
    scored = _scored(archive)
    if not scored:
        return None, []

    # --- parent ---
    if pinned and mode == "EXPLOIT":
        parent = next((r for r in archive if r.id == pinned), arch.best_record(archive))
    elif policy == "best":
        parent = arch.best_record(archive)
    elif policy == "random":
        parent = rng.choice(scored)
    else:  # "llm"
        parent = arch.best_record(archive) if mode == "EXPLOIT" else _weighted_random(archive, rng)

    # --- inspirations: top-3 + 2-diverse + recent N, deduped, capped at K ---
    top = top_by_cer(archive, 3)
    shown_fp = {tuple(r.fingerprint) for r in top}
    div = diverse_sample(archive, 2, exclude_fp=shown_fp, rng=rng)
    rec = recent_attempts(archive, recent)
    inspr = dedup_by_fingerprint([*top, *div, *rec])
    if parent is not None:
        inspr = [r for r in inspr if r.id != parent.id]
    return parent, inspr[:K]
