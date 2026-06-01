"""
harness/state.py
Serializable Phase 3 harness state.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class HarnessState:
    job_id: str
    iteration: int = 0
    best_cer: float | None = None
    best_hyp_id: str | None = None
    status: str = "running"
    # Iterations elapsed since best_cer last improved. Drives the cold-restart
    # / discovery-mode trigger in runner.build_candidate_prompt (proposal
    # 2026-05-29-prompt-diversification §4.1). advance() increments it;
    # record_best() resets it to 0. Defaults to 0 so older state files (which
    # lack the field) load unchanged — backward-compatible.
    iters_since_best_update: int = 0
    # Number of iterations that actually produced a score_report (= verify ran
    # and a candidate was scored). `iteration` counts every ATTEMPT including
    # format/command/scope rejects that never reached verify; the portfolio
    # scheduler must pace modes by evaluated work, not raw attempts, so a burst
    # of format-rejects doesn't skip ahead in the mode rotation (codex review A,
    # proposal §4.2). Defaults to 0 → old state files load unchanged.
    evaluated_count: int = 0

    @classmethod
    def load(cls, path: Path) -> "HarnessState":
        data = json.loads(path.read_text(encoding="utf-8"))
        # Ignore unknown keys so a state file written by a newer schema still
        # loads on an older codebase; missing keys fall back to field defaults.
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(
            payload,
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def advance(self) -> None:
        self.iteration += 1
        self.iters_since_best_update += 1

    def record_best(self, hyp_id: str, corpus_cer: float) -> None:
        self.best_hyp_id = hyp_id
        self.best_cer = corpus_cer
        self.iters_since_best_update = 0

    def record_evaluated(self) -> None:
        """Mark that this attempt reached verify and was scored. Called on the
        evaluated path only (not format/command/scope/verify-fail rejects)."""
        self.evaluated_count += 1
