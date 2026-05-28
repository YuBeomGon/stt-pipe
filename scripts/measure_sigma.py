"""Measure σ via representative-file proxy (`PHASE1-PLAN.md §9`).

Cost compromise: instead of running corpus × 3, run the *longest* `_l.wav`
× 3 with the current ``workspace.transcribe`` and take stdev of the three
``cer`` values. This is an approximation of corpus σ — recorded as such in
the output (``applies_to`` field).

Writes ``baseline/noise_floor.json``. Re-run is allowed only if a previous
run produced σ ≈ 0 (transcribe was broken/deterministic) — see §9.3.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from judge.metrics import per_file_metrics
from judge.normalize import normalize
from judge.pairing import pair_batch, parse_label


def _pick_representative(batch: str, profile_path: Path) -> tuple[Path, Path, float]:
    """Longest `_l.wav` by ``duration_s`` in the audio profile;
    tie-break by lexical path order.
    """
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    by_path = {entry["wav"]: entry for entry in profile["per_file"]}

    pairs = pair_batch(batch)
    enriched: list[tuple[Path, Path, float]] = []
    for wav, label in pairs:
        prof = by_path.get(str(wav))
        if prof is None:
            continue
        enriched.append((wav, label, float(prof["duration_s"])))
    if not enriched:
        raise SystemExit("no overlap between pair_batch() and audio profile")

    enriched.sort(key=lambda t: (-t[2], str(t[0])))
    return enriched[0]


def _one_run(
    wav_path: Path, label_path: Path, run_idx: int, hyp_root: Path
) -> dict[str, Any]:
    import importlib

    transcribe_mod = importlib.import_module("workspace.transcribe")
    transcribe_fn = transcribe_mod.transcribe

    telemetry_dir = hyp_root / "_telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    import os

    os.environ["ASR_TELEMETRY_DIR"] = str(telemetry_dir)
    os.environ["ASR_TELEMETRY_FILE_ID"] = wav_path.stem

    audio, sr = librosa.load(str(wav_path), sr=16000, mono=True)
    audio = audio.astype(np.float32, copy=False)
    audio_s = float(len(audio)) / float(sr)

    t0 = time.perf_counter()
    hyp = transcribe_fn(audio, sr) or ""
    decode_s = time.perf_counter() - t0

    ref_norm = normalize(parse_label(label_path))
    hyp_norm = normalize(hyp)
    m = per_file_metrics(
        ref_norm=ref_norm,
        hyp_norm=hyp_norm,
        hyp_raw=hyp,
        audio_s=audio_s,
        decode_s=decode_s,
    )
    print(
        f"run {run_idx + 1}: cer={m['cer']:.4f}  decode={decode_s:.2f}s",
        flush=True,
    )
    return m


def measure(
    batch: str,
    profile_path: Path,
    out_path: Path,
    repeats: int,
    runs_root: Path,
) -> dict[str, Any]:
    if out_path.exists():
        raise SystemExit(
            f"refusing to overwrite existing noise_floor: {out_path} — "
            "see PHASE1-PLAN §9.3"
        )

    wav_path, label_path, dur = _pick_representative(batch, profile_path)
    print(
        f"representative file: {wav_path.name} (audio_s={dur:.1f})", flush=True
    )

    cers: list[float] = []
    for i in range(repeats):
        hyp_root = runs_root / f"sigma_{i}_{int(time.time())}"
        hyp_root.mkdir(parents=True, exist_ok=True)
        m = _one_run(wav_path, label_path, i, hyp_root)
        if m["cer"] is None:
            raise SystemExit("representative file ref_chars == 0 — bad pick")
        cers.append(float(m["cer"]))

    sigma = statistics.pstdev(cers) if len(cers) >= 2 else 0.0

    payload = {
        "scope": "representative_file_proxy",
        "method": "longest _l.wav by audio_s, n runs, lexical tie-break",
        "representative_file": str(wav_path),
        "representative_audio_s": dur,
        "samples": cers,
        "sigma": sigma,
        "applies_to": (
            "corpus_cer Δ threshold (approximation — not identical to corpus σ)"
        ),
        "measured_against": "workspace.transcribe at sigma-measure time",
        "measured_at": datetime.now(UTC).isoformat(),
        "repeats": len(cers),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure noise-floor σ")
    parser.add_argument("--batch", default="AIG_녹취반출_20250715")
    parser.add_argument(
        "--profile",
        default=str(
            Path(__file__).resolve().parents[1]
            / "assets"
            / "audio_profile"
            / "AIG_녹취반출_20250715.json"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[1] / "baseline" / "noise_floor.json"
        ),
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--runs-root",
        default=str(Path(__file__).resolve().parents[1] / "runs"),
    )
    args = parser.parse_args(argv)

    result = measure(
        batch=args.batch,
        profile_path=Path(args.profile),
        out_path=Path(args.out),
        repeats=args.repeats,
        runs_root=Path(args.runs_root),
    )
    print(
        f"\nsigma={result['sigma']:.6f}  samples={result['samples']}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
