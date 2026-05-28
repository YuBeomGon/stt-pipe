"""Build per-batch audio-only profile (`PHASE1-PLAN.md §7`).

    python -m scripts.build_audio_profile --batch AIG_녹취반출_20250715

Writes ``assets/audio_profile/<batch>.json``. Only `judge/evaluate.py` and
`scripts/analyze_run.py` should read this raw file; the agent gets a
distilled view through ``runs/<hyp_id>/diagnosis_report.json``.

Never call this with the holdout batch before Phase 3 completes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import torch
from silero_vad import get_speech_timestamps, load_silero_vad

from judge.pairing import pair_batch

# Holdout names are intentionally hard-coded so a typo in --batch can't
# leak this script onto the holdout audio before Phase 3 (see AGENTS §1).
_FORBIDDEN_BATCHES = {"AIG_녹취반출_20250813"}
_TARGET_SR = 16000


def _rms_db(audio: np.ndarray, frame_length: int = 2048, hop_length: int = 512) -> np.ndarray:
    rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop_length)[0]
    # avoid log(0)
    rms = np.maximum(rms, 1e-10)
    return 20.0 * np.log10(rms)


def _percentile(arr: np.ndarray, q: float) -> float:
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def _file_profile(
    wav_path: Path,
    vad_model,
    sample_rate: int = _TARGET_SR,
) -> dict[str, Any]:
    audio, sr = librosa.load(str(wav_path), sr=sample_rate, mono=True)
    audio = audio.astype(np.float32, copy=False)
    duration_s = float(len(audio)) / float(sr)

    tensor = torch.from_numpy(audio)
    timestamps = get_speech_timestamps(
        tensor,
        vad_model,
        sampling_rate=sr,
        return_seconds=True,
    )
    segments: list[list[float]] = [
        [float(ts["start"]), float(ts["end"])] for ts in timestamps
    ]
    speech_total = sum(end - start for start, end in segments)
    silence_total = max(0.0, duration_s - speech_total)

    longest_silence = 0.0
    if segments:
        prev_end = 0.0
        for start, end in segments:
            gap = start - prev_end
            if gap > longest_silence:
                longest_silence = gap
            prev_end = end
        tail = duration_s - prev_end
        if tail > longest_silence:
            longest_silence = tail
    else:
        longest_silence = duration_s

    longest_speech = max((end - start for start, end in segments), default=0.0)

    db = _rms_db(audio)

    return {
        "wav": str(wav_path),
        "duration_s": duration_s,
        "speech_segments": segments,
        "silence_ratio": silence_total / duration_s if duration_s > 0 else None,
        "longest_silence_s": float(longest_silence),
        "longest_speech_s": float(longest_speech),
        "rms_db_mean": float(np.mean(db)),
        "rms_db_p05": _percentile(db, 5),
        "rms_db_p95": _percentile(db, 95),
    }


def build(batch: str, out_dir: Path) -> Path:
    if batch in _FORBIDDEN_BATCHES:
        raise SystemExit(
            f"refusing to profile holdout batch {batch!r}: see AGENTS.md / "
            "PHASE3-PLAN.md §6.3"
        )

    pairs = pair_batch(batch)
    if not pairs:
        raise SystemExit(f"no _l pairs found for batch {batch!r}")

    vad_model = load_silero_vad()

    per_file: list[dict[str, Any]] = []
    for wav_path, _label in pairs:
        per_file.append(_file_profile(wav_path, vad_model))
        print(
            f"profiled {wav_path.name}: dur={per_file[-1]['duration_s']:.1f}s "
            f"segments={len(per_file[-1]['speech_segments'])}",
            flush=True,
        )

    payload = {
        "batch": batch,
        "method": "silero-vad (silero-vad pip pkg) + RMS aggregates (librosa)",
        "produced_at": datetime.now(UTC).isoformat(),
        "per_file": per_file,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{batch}.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build audio profile JSON")
    parser.add_argument("--batch", default="AIG_녹취반출_20250715")
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parents[1] / "assets" / "audio_profile"),
    )
    args = parser.parse_args(argv)

    out_path = build(batch=args.batch, out_dir=Path(args.out_dir))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
