"""CER metrics + corpus aggregation.

`STT-PIPELINE-SPEC.md §5, §6`. Hallucination patterns from §6.4 are applied
against the *raw* hypothesis (before normalization).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from typing import Any

from rapidfuzz.distance import Levenshtein

# §6.4 blocking patterns — Whisper Korean hallucination set.
HALLUCINATION_PATTERNS: tuple[str, ...] = (
    r"기상캐스터\s*배혜지",
    r"시청자여러분",
    r"이\s*시각\s*세계였습니다",
    r"시청해주셔서\s*감사합니다",
    r"한글자막\s*by\s*.+",
    r"다음\s*영상에서\s*만나요",
    r"자막\s*제공",
    r"광고를\s*포함",
)
_HALLUCINATION_RE = [re.compile(p, re.IGNORECASE) for p in HALLUCINATION_PATTERNS]


def _hallucination_scan(hyp_raw: str) -> tuple[int, list[dict[str, Any]]]:
    spans: list[dict[str, Any]] = []
    for pat, regex in zip(HALLUCINATION_PATTERNS, _HALLUCINATION_RE):
        for m in regex.finditer(hyp_raw or ""):
            spans.append(
                {"pattern": pat, "start": m.start(), "end": m.end(), "text": m.group(0)}
            )
    return len(spans), spans


def _is_repeated_text(hyp_norm: str, n: int = 4, min_repeat: int = 3) -> bool:
    """True if some n-gram (char-level) appears ≥ ``min_repeat`` times
    consecutively in the normalized hypothesis. Simple heuristic for
    `repeated_text_rate` (`STT-PIPELINE-SPEC.md §6.2`).
    """
    if len(hyp_norm) < n * min_repeat:
        return False
    for i in range(len(hyp_norm) - n * min_repeat + 1):
        gram = hyp_norm[i : i + n]
        ok = all(
            hyp_norm[i + k * n : i + (k + 1) * n] == gram for k in range(min_repeat)
        )
        if ok:
            return True
    return False


def per_file_metrics(
    ref_norm: str,
    hyp_norm: str,
    hyp_raw: str,
    audio_s: float,
    decode_s: float,
    audio_coverage_s: float | None = None,
) -> dict[str, Any]:
    """One file's metrics — see `STT-PIPELINE-SPEC.md §5.3 / §6.3`."""

    ref_chars = len(ref_norm)
    hyp_chars = len(hyp_norm)
    empty_output = hyp_chars == 0

    ops = Levenshtein.editops(ref_norm, hyp_norm)
    sub = sum(1 for o in ops if o.tag == "replace")
    delete = sum(1 for o in ops if o.tag == "delete")
    ins = sum(1 for o in ops if o.tag == "insert")
    edits = sub + delete + ins

    if ref_chars == 0:
        cer: float | None = None
        length_ratio: float | None = None
    else:
        cer = edits / ref_chars
        length_ratio = hyp_chars / ref_chars

    rtf = (decode_s / audio_s) if audio_s > 0 else None
    hits, spans = _hallucination_scan(hyp_raw)
    repeated = _is_repeated_text(hyp_norm)

    return {
        "ref_chars": ref_chars,
        "hyp_chars": hyp_chars,
        "length_ratio": length_ratio,
        "empty_output": empty_output,
        "audio_s": audio_s,
        "decode_s": decode_s,
        "cer": cer,
        "rtf": rtf,
        "sub": sub,
        "del": delete,
        "ins": ins,
        "edits": edits,
        "audio_coverage_s": audio_coverage_s,
        "hallucination_hits": hits,
        "hallucinated_spans": spans,
        "repeated_text": repeated,
    }


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    idx = q * (len(xs) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def corpus_aggregate(per_file: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-file metrics into corpus-level summary (`§5.2 / §6.2`)."""

    files = list(per_file)
    n = len(files)
    if n == 0:
        raise ValueError("corpus_aggregate: empty per_file list")

    # Files with an empty reference (e.g. a label that contains only
    # turn-number markers) cannot contribute to character-weighted CER and
    # would otherwise inflate the numerator with pure-insertion edits.
    # Keep them in per_file.jsonl for transparency but exclude from the
    # corpus_cer / edit-breakdown sums.
    scored = [f for f in files if f["ref_chars"] > 0]
    num_scored = len(scored)
    total_ref = sum(f["ref_chars"] for f in scored)
    total_edits = sum(f["edits"] for f in scored)
    total_sub = sum(f["sub"] for f in scored)
    total_del = sum(f["del"] for f in scored)
    total_ins = sum(f["ins"] for f in scored)
    total_audio = sum(f["audio_s"] for f in files)
    total_decode = sum(f["decode_s"] for f in files)

    cer_values = [f["cer"] for f in scored if f["cer"] is not None]
    macro_cer = statistics.fmean(cer_values) if cer_values else None
    corpus_cer = (total_edits / total_ref) if total_ref > 0 else None

    if total_edits > 0:
        breakdown = {
            "sub_ratio": total_sub / total_edits,
            "del_ratio": total_del / total_edits,
            "ins_ratio": total_ins / total_edits,
        }
    else:
        breakdown = {"sub_ratio": 0.0, "del_ratio": 0.0, "ins_ratio": 0.0}

    length_ratios = [f["length_ratio"] for f in files if f["length_ratio"] is not None]
    if length_ratios:
        length_ratio_block = {
            "mean": statistics.fmean(length_ratios),
            "p05": _quantile(length_ratios, 0.05),
            "p95": _quantile(length_ratios, 0.95),
        }
    else:
        length_ratio_block = {"mean": None, "p05": None, "p95": None}

    empty_rate = sum(1 for f in files if f["empty_output"]) / n
    repeated_rate = sum(1 for f in files if f["repeated_text"]) / n
    hits_total = sum(f["hallucination_hits"] for f in files)
    hit_rate = sum(1 for f in files if f["hallucination_hits"] > 0) / n

    coverage_vals = [
        f["audio_coverage_s"] for f in files if f["audio_coverage_s"] is not None
    ]
    if coverage_vals and total_audio > 0:
        audio_coverage_rate: float | None = sum(coverage_vals) / total_audio
    else:
        audio_coverage_rate = None

    rtf_values = [f["rtf"] for f in files if f["rtf"] is not None]
    avg_rtf = statistics.fmean(rtf_values) if rtf_values else None

    runtime_per_min = (
        total_decode / (total_audio / 60.0) if total_audio > 0 else None
    )

    return {
        "num_files": n,
        "num_files_scored": num_scored,
        "corpus_cer": corpus_cer,
        "macro_cer": macro_cer,
        "error_breakdown": breakdown,
        "empty_output_rate": empty_rate,
        "length_ratio": length_ratio_block,
        "repeated_text_rate": repeated_rate,
        "audio_coverage_rate": audio_coverage_rate,
        "hallucination_hit_rate": hit_rate,
        "hallucination_hits_total": hits_total,
        "total_audio_s": total_audio,
        "total_inference_time_s": total_decode,
        "runtime_s_per_audio_min": runtime_per_min,
        "avg_rtf": avg_rtf,
    }
