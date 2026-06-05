"""Measure the faster-whisper baseline (`PHASE1-PLAN.md §8`).

One-time operation. Writes ``baseline/target_cer.json`` and the artefact MUST
NOT be re-measured/overwritten (`STT-PIPELINE-SPEC.md §7, §11`).

The output file carries two distinct numbers:
    - ``target_cer``  : the *manual* success goal (currently 0.10). Set once
                       at measurement time via ``--target-cer``. Phase 3
                       keep/revert is judged against this.
    - ``baseline_cer``: the measured faster-whisper corpus_cer. Comparison
                       anchor only; not a pass/fail threshold.

If a future ambition change requires updating *only* ``target_cer`` without
re-measuring the baseline, hand-edit the field with a clear commit message
rather than re-running this script — re-running is refused while the output
already exists, by design.
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


# Production "LEFT" (channel 0 = "_l", 상담사) faster-whisper decoding/VAD
# params, lifted verbatim from stt-engine configs/domains/aig.yaml. SAME stock
# base model (large-v3-turbo) — NOT the fine-tuned ct2 model — and decoding/VAD
# only: no preprocess (speed/highpass), no postprocess/split/prompt_correct.
# Used to re-anchor the baseline against a realistically-tuned decoder rather
# than the bare beam=5 default.
_PROD_LEFT_INITIAL_PROMPT = (
    "이 내용은 보험 상담 전화 통화입니다. 주요 보험 용어는 AIG 손해보험, 보험료, "
    "보험금, 보장개시일, 면책기간, 약관, 특약 등입니다."
)
_PROD_LEFT_DECODING = {
    "language": "ko",
    "task": "transcribe",
    "beam_size": 10,
    "best_of": 10,
    "patience": 1.0,
    "length_penalty": 1.0,
    "repetition_penalty": 1.05,
    "no_repeat_ngram_size": 5,
    "suppress_blank": True,
    "compression_ratio_threshold": 2.2,
    "log_prob_threshold": -1.0,
    "no_speech_threshold": 0.6,
    "condition_on_previous_text": True,
    "initial_prompt": _PROD_LEFT_INITIAL_PROMPT,
    "word_timestamps": True,
    "without_timestamps": False,
    "chunk_length": 30,
    "vad_filter": True,
    "vad_parameters": {
        "threshold": 0.5,
        "min_speech_duration_ms": 400,
        "max_speech_duration_s": float("inf"),
        "min_silence_duration_ms": 1000,
        "speech_pad_ms": 400,
    },
}


def measure(
    batch: str,
    out_path: Path,
    model_size: str = "large-v3-turbo",
    device: str = "cuda",
    compute_type: str = "float16",
    beam_size: int = 5,
    language: str = "ko",
    vad_filter: bool = True,
    target_cer: float = 0.10,
    decoding_params: dict[str, Any] | None = None,
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

    if decoding_params is None:
        decoding_params = {
            "language": language,
            "task": "transcribe",
            "beam_size": beam_size,
            "vad_filter": vad_filter,
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
        cer_str = f"{cer:.4f}" if cer is not None else "n/a"
        print(
            f"{wav_path.name}  cer={cer_str}  audio={audio_s:.1f}s  decode={decode_s:.2f}s",
            flush=True,
        )

    agg = corpus_aggregate(per_file)

    # Phase 3 success criterion is the manual goal (`target_cer`), not the
    # measured baseline. `baseline_cer` is the off-the-shelf faster-whisper
    # number we are trying to beat. Decoupling lets us aim past faster-whisper
    # without re-sealing the baseline if we change the ambition.
    baseline: dict[str, Any] = {
        "target_cer": target_cer,
        "baseline_cer": agg["corpus_cer"],
        "macro_cer": agg["macro_cer"],
        "num_files": agg["num_files"],
        "num_files_scored": agg.get("num_files_scored"),
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
    parser.add_argument("--no-vad-filter", dest="vad_filter", action="store_false")
    parser.set_defaults(vad_filter=True)
    parser.add_argument(
        "--preset",
        choices=["default", "prod-left"],
        default="default",
        help=(
            "default = bare beam=5 decoder (original sealed baseline). "
            "prod-left = stt-engine 상담사(_l) faster-whisper decoding/VAD "
            "params on the same stock base model (no pre/postprocess)."
        ),
    )
    parser.add_argument(
        "--target-cer",
        type=float,
        default=0.10,
        help=(
            "manual success goal sealed into target_cer.json (Phase 3 "
            "keep/revert and success criterion). Independent from the "
            "measured faster-whisper baseline_cer. Set once at initial "
            "measurement; do not re-run this script to change it — "
            "hand-edit target_cer in the sealed file instead."
        ),
    )
    args = parser.parse_args(argv)

    result = measure(
        batch=args.batch,
        out_path=Path(args.out),
        model_size=args.model_size,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        vad_filter=args.vad_filter,
        target_cer=args.target_cer,
        decoding_params=dict(_PROD_LEFT_DECODING) if args.preset == "prod-left" else None,
    )
    print(
        f"\ntarget_cer={result['target_cer']:.6f}  "
        f"baseline_cer={result['baseline_cer']:.6f}  "
        f"total_inference_time_s={result['total_inference_time_s']:.2f}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
