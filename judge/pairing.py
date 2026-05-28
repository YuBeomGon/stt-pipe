"""Label-driven pairing of `*_l.wav` / `*_l.txt` files.

`STT-PIPELINE-SPEC.md §3.3 / §3.4` — left channel only, label is source of truth.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def _default_root() -> Path:
    env = os.environ.get("ASR_RAW_DATA_ROOT")
    if env:
        return Path(env)
    # Project-relative default.
    return Path(__file__).resolve().parents[1] / "data" / "raw"


def pair_batch(
    batch_name: str,
    root: Path | None = None,
) -> list[tuple[Path, Path]]:
    """Return list of `(wav_path, label_path)` for the given batch.

    Rules (`STT-PIPELINE-SPEC.md §3.4`):
        1. Iterate `<root>/label/<batch>/*.txt`.
        2. Keep only `*_l.txt` (left channel).
        3. Pair with matching `<root>/wav/<batch>/<stem>.wav`.
        4. Skip + warn when the wav is missing.
        5. Output sorted by wav path for deterministic order.
    """
    if root is None:
        root = _default_root()

    label_dir = Path(root) / "label" / batch_name
    wav_dir = Path(root) / "wav" / batch_name

    if not label_dir.is_dir():
        raise FileNotFoundError(f"label dir not found: {label_dir}")
    if not wav_dir.is_dir():
        raise FileNotFoundError(f"wav dir not found: {wav_dir}")

    pairs: list[tuple[Path, Path]] = []
    for label_path in sorted(label_dir.glob("*.txt")):
        if not label_path.name.endswith("_l.txt"):
            continue
        wav_path = wav_dir / (label_path.stem + ".wav")
        if not wav_path.is_file():
            log.warning("pairing: wav missing for label %s", label_path)
            continue
        pairs.append((wav_path, label_path))

    return sorted(pairs, key=lambda t: str(t[0]))


def parse_label(path: Path) -> str:
    """Parse a turn-structured label file into a single-line reference.

    `STT-PIPELINE-SPEC.md §4.2`:
        - lines that contain only digits (turn numbers) are discarded
        - blank lines are discarded
        - remaining lines are concatenated with a single space
    """
    text = Path(path).read_text(encoding="utf-8")
    parts: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.isdigit():
            continue
        parts.append(line)
    return " ".join(parts)
