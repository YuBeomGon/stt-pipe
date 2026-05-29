"""
harness/state.py
Serializable Phase 3 harness state.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class HarnessState:
    job_id: str
    iteration: int = 0
    best_cer: float | None = None
    best_hyp_id: str | None = None
    status: str = "running"

    @classmethod
    def load(cls, path: Path) -> "HarnessState":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

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

    def record_best(self, hyp_id: str, corpus_cer: float) -> None:
        self.best_hyp_id = hyp_id
        self.best_cer = corpus_cer
