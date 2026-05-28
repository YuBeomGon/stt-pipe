"""Judge entry — runs ``transcribe`` over a batch and writes the three
artefacts that autoresearch reads (`STT-PIPELINE-SPEC.md §5/§6`,
`DESIGN.md §2.5`):

    runs/<hyp_id>/score_report.json
    runs/<hyp_id>/per_file.jsonl
    runs/<hyp_id>/diagnosis_report.json

The last line written to stdout is the corpus_cer scalar, which Phase 3
verify parses.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import librosa
import numpy as np

from judge.diagnosis import build_diagnosis
from judge.metrics import corpus_aggregate, per_file_metrics
from judge.normalize import normalize
from judge.pairing import pair_batch, parse_label

log = logging.getLogger("judge.evaluate")


def _load_transcribe(spec: str) -> Callable[[np.ndarray, int], str]:
    """Resolve ``module.path:callable``."""
    if ":" not in spec:
        raise ValueError(f"--transcribe must be 'module:callable', got {spec!r}")
    mod_name, func_name = spec.split(":", 1)
    module = importlib.import_module(mod_name)
    fn = getattr(module, func_name)
    if not callable(fn):
        raise TypeError(f"{spec} is not callable")
    return fn


def _read_coverage(telemetry_dir: Path, file_id: str) -> float | None:
    """Sum ``end - start`` over entries in the sidecar JSONL, if present."""
    path = telemetry_dir / f"{file_id}.jsonl"
    if not path.is_file():
        return None
    total = 0.0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                start = obj.get("start")
                end = obj.get("end")
                if start is None or end is None:
                    continue
                total += max(0.0, float(end) - float(start))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("telemetry read failed for %s: %s", file_id, exc)
        return None
    return total


def evaluate_batch(
    batch: str,
    transcribe_spec: str,
    out_path: Path,
    sample_rate: int = 16000,
    profile_root: Path | None = None,
) -> dict[str, Any]:
    transcribe_fn = _load_transcribe(transcribe_spec)

    out_path = Path(out_path)
    hyp_root = out_path.parent
    hyp_root.mkdir(parents=True, exist_ok=True)
    telemetry_dir = hyp_root / "_telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)

    if profile_root is None:
        profile_root = Path(__file__).resolve().parents[1] / "assets" / "audio_profile"

    pairs = pair_batch(batch)
    if not pairs:
        raise RuntimeError(f"no _l pairs found for batch {batch!r}")

    per_file_records: list[dict[str, Any]] = []
    per_file_path = hyp_root / "per_file.jsonl"
    with per_file_path.open("w", encoding="utf-8") as pf_fh:
        for wav_path, label_path in pairs:
            file_id = wav_path.stem
            os.environ["ASR_TELEMETRY_DIR"] = str(telemetry_dir)
            os.environ["ASR_TELEMETRY_FILE_ID"] = file_id

            audio, sr = librosa.load(str(wav_path), sr=sample_rate, mono=True)
            audio = audio.astype(np.float32, copy=False)
            audio_s = float(len(audio)) / float(sr) if sr else 0.0

            t0 = time.perf_counter()
            try:
                hyp_raw = transcribe_fn(audio, sr)
            except Exception:
                log.exception("transcribe failed on %s", wav_path)
                raise
            decode_s = time.perf_counter() - t0
            if hyp_raw is None:
                hyp_raw = ""
            if not isinstance(hyp_raw, str):
                raise TypeError(
                    f"transcribe must return str, got {type(hyp_raw).__name__}"
                )

            ref_raw = parse_label(label_path)
            ref_norm = normalize(ref_raw)
            hyp_norm = normalize(hyp_raw)

            coverage_s = _read_coverage(telemetry_dir, file_id)
            metrics = per_file_metrics(
                ref_norm=ref_norm,
                hyp_norm=hyp_norm,
                hyp_raw=hyp_raw,
                audio_s=audio_s,
                decode_s=decode_s,
                audio_coverage_s=coverage_s,
            )
            record = {"wav": str(wav_path), "label": str(label_path), **metrics}
            per_file_records.append(record)
            pf_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            log.info(
                "scored %s cer=%s decode_s=%.2f hits=%d",
                wav_path.name,
                f"{metrics['cer']:.4f}" if metrics["cer"] is not None else "n/a",
                decode_s,
                metrics["hallucination_hits"],
            )

    aggregate = corpus_aggregate(per_file_records)
    score_report = {
        "batch": batch,
        "produced_at": datetime.now(UTC).isoformat(),
        "transcribe_spec": transcribe_spec,
        **aggregate,
    }
    out_path.write_text(
        json.dumps(score_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    diag = build_diagnosis(
        per_file_records=per_file_records,
        batch_name=batch,
        profile_root=Path(profile_root),
    )
    (hyp_root / "diagnosis_report.json").write_text(
        json.dumps(diag, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return score_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge evaluate")
    parser.add_argument("--batch", required=True)
    parser.add_argument(
        "--transcribe", required=True,
        help="module path 'pkg.mod:callable' (e.g. workspace.transcribe:transcribe)",
    )
    parser.add_argument("--out", required=True, help="path to score_report.json")
    parser.add_argument("--sample-rate", type=int, default=16000)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("ASR_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    report = evaluate_batch(
        batch=args.batch,
        transcribe_spec=args.transcribe,
        out_path=Path(args.out),
        sample_rate=args.sample_rate,
    )

    # Last stdout line MUST be the corpus_cer scalar — autoresearch parses it.
    cer = report.get("corpus_cer")
    print(f"{cer}" if cer is not None else "nan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
