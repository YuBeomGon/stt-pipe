"""Build `runs/<hyp_id>/diagnosis_report.json`.

Combines per-file metrics with the (judge-only) audio profile summary so the
agent gets a single curated view per iteration. Raw `speech_segments` are
intentionally excluded — see `docs/PHASE3-LOOP.md §3`.
"""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _summarize_segments(segments: list[list[float]]) -> dict[str, Any]:
    """Reduce a raw `[[start, end], ...]` list to distribution stats only."""
    if not segments:
        return {
            "num_segments": 0,
            "speech_s_p50": None,
            "speech_s_p95": None,
            "silence_gap_p95": None,
        }
    durations = [max(0.0, e - s) for s, e in segments]
    gaps = [
        max(0.0, segments[i + 1][0] - segments[i][1])
        for i in range(len(segments) - 1)
    ]
    return {
        "num_segments": len(segments),
        "speech_s_p50": (
            statistics.median(durations) if durations else None
        ),
        "speech_s_p95": _quantile(durations, 0.95),
        "silence_gap_p95": _quantile(gaps, 0.95) if gaps else None,
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


def _load_audio_profile(batch_name: str, profile_root: Path) -> dict[str, dict] | None:
    """Return {wav_path -> profile_entry} for the batch, or None if missing."""
    path = profile_root / f"{batch_name}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {entry["wav"]: entry for entry in data.get("per_file", [])}


def _flags(metrics: dict, length_ratio_p05_threshold: float = 0.5) -> list[str]:
    out: list[str] = []
    if metrics.get("empty_output"):
        out.append("empty_output")
    lr = metrics.get("length_ratio")
    if lr is not None and lr < length_ratio_p05_threshold:
        out.append("length_ratio_low")
    if lr is not None and lr > 2.0:
        out.append("length_ratio_high")
    if metrics.get("hallucination_hits", 0) > 0:
        out.append("hallucination_hit")
    if metrics.get("repeated_text"):
        out.append("repeated_text")
    return out


def _select_focus(
    per_file_diag: list[dict[str, Any]], max_focus: int = 2
) -> list[dict[str, Any]]:
    """Deterministic focus-file selection.

    Priority signals (from PHASE1-PLAN §3.2):
        1. guard violation count
        2. per-file CER
        3. (baseline delta — not available in Phase 1)
    Tie-break: lexical path sort.

    ``why_selected`` reports which signal actually placed the file in focus,
    not the global ranking criteria — it labels ``worst_cer`` only for the
    file with the highest CER in the corpus, and ``guard_violation`` only
    for files that actually have flags.
    """

    def sort_key(entry: dict[str, Any]):
        flags = entry.get("flags", [])
        cer = entry["metrics"].get("cer")
        # Lower (more negative) sorts first → use negated values.
        return (-len(flags), -(cer if cer is not None else -1.0), entry["wav"])

    cer_values = [
        e["metrics"]["cer"]
        for e in per_file_diag
        if e["metrics"].get("cer") is not None
    ]
    worst_cer_value = max(cer_values) if cer_values else None

    ranked = sorted(per_file_diag, key=sort_key)
    selected: list[dict[str, Any]] = []
    for entry in ranked[:max_focus]:
        reasons: list[str] = []
        if entry.get("flags"):
            reasons.append("guard_violation")
        cer = entry["metrics"].get("cer")
        if (
            worst_cer_value is not None
            and cer is not None
            and cer == worst_cer_value
        ):
            reasons.append("worst_cer")
        if not reasons:
            reasons.append("lexical_top")
        selected.append({"wav": entry["wav"], "why_selected": reasons})
    return selected


def build_diagnosis(
    per_file_records: list[dict[str, Any]],
    batch_name: str,
    profile_root: Path,
    max_focus: int = 2,
) -> dict[str, Any]:
    """Compose `diagnosis_report.json` content.

    ``per_file_records`` is the same list written to ``per_file.jsonl`` —
    each item must contain ``wav`` and the metrics returned by
    ``judge.metrics.per_file_metrics``.
    """

    profile_index = _load_audio_profile(batch_name, profile_root)
    if profile_index is None:
        log.warning(
            "diagnosis: audio profile missing for %s — emitting metrics-only",
            batch_name,
        )

    per_file_diag: list[dict[str, Any]] = []
    for rec in per_file_records:
        wav = rec["wav"]
        metrics_block = {
            "cer": rec.get("cer"),
            "length_ratio": rec.get("length_ratio"),
            "hallucination_hits": rec.get("hallucination_hits", 0),
            "empty_output": rec.get("empty_output", False),
            "repeated_text": rec.get("repeated_text", False),
        }

        profile_summary: dict[str, Any] | None = None
        if profile_index is not None:
            prof = profile_index.get(wav) or profile_index.get(str(Path(wav)))
            if prof is not None:
                profile_summary = {
                    "duration_s": prof.get("duration_s"),
                    "silence_ratio": prof.get("silence_ratio"),
                    "longest_silence_s": prof.get("longest_silence_s"),
                    "longest_speech_s": prof.get("longest_speech_s"),
                    "rms_db_mean": prof.get("rms_db_mean"),
                    "rms_db_p05": prof.get("rms_db_p05"),
                    "rms_db_p95": prof.get("rms_db_p95"),
                    "speech_segment_summary": _summarize_segments(
                        prof.get("speech_segments", [])
                    ),
                }

        entry = {
            "wav": wav,
            "metrics": metrics_block,
            "audio_profile_summary": profile_summary,
            "flags": _flags(rec),
        }
        per_file_diag.append(entry)

    return {
        "batch": batch_name,
        "per_file_diagnosis": per_file_diag,
        "focus_files": _select_focus(per_file_diag, max_focus=max_focus),
    }
