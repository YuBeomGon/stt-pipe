"""Measure the faster-whisper baseline (`PHASE1-PLAN.md §8`).

One-time operation. Writes ``baseline/target_cer.json`` and the artefact MUST
NOT be re-measured/overwritten (`STT-PIPELINE-SPEC.md §7, §11`).
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from judge.metrics import corpus_aggregate, per_file_metrics
from judge.normalize import normalize
from judge.pairing import pair_batch, parse_label


_FORBIDDEN_BATCHES = {"AIG_녹취반출_20250813"}


def _gather_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for mod in ("ctranslate2", "faster_whisper", "transformers", "librosa", "rapidfuzz"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
        except Exception:  # pragma: no cover
            out[mod] = "missing"
    return out


def _hardware_block() -> dict[str, Any]:
    info: dict[str, Any] = {
        "host": socket.gethostname(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["cuda_version"] = torch.version.cuda
    except Exception:  # pragma: no cover
        pass
    return info


def _hf_revision(model_name: str) -> str | None:
    try:
        from huggingface_hub import HfApi

        api = HfApi()
        info = api.model_info(model_name)
        return getattr(info, "sha", None)
    except Exception:
        return None


def measure(
    batch: str,
    out_path: Path,
    model_size: str = "large-v3-turbo",
    device: str = "cuda",
    compute_type: str = "float16",
    beam_size: int = 5,
    language: str = "ko",
) -> dict[str, Any]:
    if batch in _FORBIDDEN_BATCHES:
        raise SystemExit(f"refusing to measure baseline on holdout {batch!r}")

    if out_path.exists():
        raise SystemExit(
            f"refusing to overwrite existing baseline: {out_path} — see "
            "STT-PIPELINE-SPEC.md §7 (sealed once)"
        )

    pairs = pair_batch(batch)
    if not pairs:
        raise SystemExit(f"no _l pairs for batch {batch!r}")

    from faster_whisper import WhisperModel  # local import to keep script importable

    print(
        f"loading faster-whisper {model_size!r} device={device} compute={compute_type}",
        flush=True,
    )
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    decoding_params = {
        "language": language,
        "task": "transcribe",
        "beam_size": beam_size,
        "vad_filter": False,
        "without_timestamps": True,
        "condition_on_previous_text": False,
    }

    per_file: list[dict[str, Any]] = []
    for wav_path, label_path in pairs:
        audio, sr = librosa.load(str(wav_path), sr=16000, mono=True)
        audio = audio.astype(np.float32, copy=False)
        audio_s = float(len(audio)) / float(sr)

        t0 = time.perf_counter()
        segments, _info = model.transcribe(audio, **decoding_params)
        text = "".join(seg.text for seg in segments)
        decode_s = time.perf_counter() - t0

        ref_norm = normalize(parse_label(label_path))
        hyp_norm = normalize(text)
        m = per_file_metrics(
            ref_norm=ref_norm,
            hyp_norm=hyp_norm,
            hyp_raw=text,
            audio_s=audio_s,
            decode_s=decode_s,
        )
        per_file.append({"wav": str(wav_path), "label": str(label_path), **m})
        cer = m["cer"]
        print(
            f"{wav_path.name}  cer={cer:.4f}  audio={audio_s:.1f}s  decode={decode_s:.2f}s",
            flush=True,
        )

    agg = corpus_aggregate(per_file)

    baseline: dict[str, Any] = {
        "target_cer": agg["corpus_cer"],
        "macro_cer": agg["macro_cer"],
        "num_files": agg["num_files"],
        "batches": [batch],
        "total_audio_s": agg["total_audio_s"],
        "total_inference_time_s": agg["total_inference_time_s"],
        "runtime_s_per_audio_min": agg["runtime_s_per_audio_min"],
        "avg_rtf": agg["avg_rtf"],
        "guard_baseline": {
            "empty_output_rate": agg["empty_output_rate"],
            "length_ratio": agg["length_ratio"],
            "repeated_text_rate": agg["repeated_text_rate"],
            "audio_coverage_rate": agg["audio_coverage_rate"],
            "hallucination_hit_rate": agg["hallucination_hit_rate"],
            "hallucination_hits_total": agg["hallucination_hits_total"],
        },
        "versions": _gather_versions(),
        "model": {
            "name": "openai/whisper-large-v3-turbo",
            "revision": _hf_revision("openai/whisper-large-v3-turbo"),
            "quantization": compute_type,
            "library": "faster-whisper",
            "model_size_or_path": model_size,
        },
        "decoding_params": decoding_params,
        "hardware": _hardware_block(),
        "produced_at": datetime.now(UTC).isoformat(),
        "per_file": per_file,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return baseline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure faster-whisper baseline")
    parser.add_argument("--batch", default="AIG_녹취반출_20250715")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "baseline" / "target_cer.json"),
    )
    parser.add_argument("--model-size", default="large-v3-turbo")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--beam-size", type=int, default=5)
    args = parser.parse_args(argv)

    result = measure(
        batch=args.batch,
        out_path=Path(args.out),
        model_size=args.model_size,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
    )
    print(
        f"\ntarget_cer={result['target_cer']:.6f}  "
        f"total_inference_time_s={result['total_inference_time_s']:.2f}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
