"""Phase 3 사후 분석 — `docs/PHASE2-PLAN.md` §3 구현.

    python -m scripts.analyze_run \
        --runs-dir runs/ \
        --baseline baseline/ \
        --template docs/templates/REPORT.md \
        --out runs/_summary/REPORT.md

각 `runs/<hyp_id>/` 의 `score_report.json` / `per_file.jsonl` /
`diagnosis_report.json` 시계열을 8 개 축 (A~H) 으로 정리해 템플릿 변수
자리에 치환. 사람 판단 칸 `<!-- TODO: ... -->` 는 그대로 둔다.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("analyze_run")

_EXCLUDED_DIRNAMES = {"_summary", "holdout", "_telemetry"}
_NOISE_DEFAULT_DELTA = 0.01  # absolute Δcer fallback when sigma is provisional

# Category keyword groups — PHASE2-PLAN §3.3 D.
_CATEGORY_PATTERNS = {
    "chunking": re.compile(r"\b(chunk|window|split|segment)", re.IGNORECASE),
    "prompt": re.compile(r"\b(prompt|token|language)", re.IGNORECASE),
    "decode": re.compile(r"\b(beam|temperature|fallback|sample)", re.IGNORECASE),
    "post": re.compile(r"\b(dedup|merge|regex|postprocess|post-process)", re.IGNORECASE),
}

# Keyword → expected metric direction (for H. reasoning auto-alignment).
# value: (metric_key_in_score_report, expected_sign)
#   expected_sign = -1 means metric should *decrease* on alignment
#                 = +1 means metric should *increase* on alignment
_REASONING_RULES: dict[str, tuple[str, int]] = {
    "환각": ("hallucination_hit_rate", -1),
    "hallucination": ("hallucination_hit_rate", -1),
    "halluc": ("hallucination_hit_rate", -1),
    "ins_ratio": ("error_breakdown.ins_ratio", -1),
    "삽입": ("error_breakdown.ins_ratio", -1),
    "deletion": ("error_breakdown.del_ratio", -1),
    "del_ratio": ("error_breakdown.del_ratio", -1),
    "누락": ("error_breakdown.del_ratio", -1),
    "repeat": ("repeated_text_rate", -1),
    "반복": ("repeated_text_rate", -1),
    "length": ("length_ratio.mean", +1),  # length up = closer to ref when stub under-emits
    "길이": ("length_ratio.mean", +1),
    "empty": ("empty_output_rate", -1),
    "coverage": ("audio_coverage_rate", +1),
}


# --------------------------------------------------------------------------- #
# data carrier                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class IterRecord:
    hyp_id: str
    produced_at: datetime
    score: dict[str, Any]
    per_file: list[dict[str, Any]]
    diagnosis: dict[str, Any] | None = None
    commit_sha: str | None = None
    commit_subject: str | None = None
    commit_ts: datetime | None = None

    # populated by classify
    accepted: bool = False
    delta_from_best: float | None = None
    guard_flags: list[str] = field(default_factory=list)
    category: str = "unclassified"

    @property
    def corpus_cer(self) -> float | None:
        return self.score.get("corpus_cer")

    def metric(self, dotted: str) -> float | None:
        """Resolve dotted metric key against the score report."""
        node: Any = self.score
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return None
        return node if isinstance(node, (int, float)) else None


# --------------------------------------------------------------------------- #
# loaders                                                                     #
# --------------------------------------------------------------------------- #


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                log.warning("bad jsonl line in %s: %s", path, exc)
    return out


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("bad json %s: %s", path, exc)
        return None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def discover_iterations(runs_dir: Path) -> list[IterRecord]:
    """Walk ``runs/`` and load every hyp_id directory with a score_report."""
    out: list[IterRecord] = []
    for child in sorted(runs_dir.iterdir() if runs_dir.is_dir() else []):
        if not child.is_dir() or child.name in _EXCLUDED_DIRNAMES:
            continue
        score = _read_json(child / "score_report.json")
        if score is None:
            continue
        per_file = _read_jsonl(child / "per_file.jsonl")
        diagnosis = _read_json(child / "diagnosis_report.json")
        produced_at = _parse_iso(score.get("produced_at")) or datetime.fromtimestamp(
            (child / "score_report.json").stat().st_mtime
        )
        out.append(
            IterRecord(
                hyp_id=child.name,
                produced_at=produced_at,
                score=score,
                per_file=per_file,
                diagnosis=diagnosis,
            )
        )
    out.sort(key=lambda r: r.produced_at)
    return out


# --------------------------------------------------------------------------- #
# git enrichment                                                              #
# --------------------------------------------------------------------------- #


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def enrich_with_git(iters: list[IterRecord]) -> None:
    """Best-effort: match each iter's produced_at to the nearest preceding commit.

    Phase 3 autoresearch is expected to commit per hypothesis; the closest
    earlier commit by timestamp is taken as the iteration's commit. This is a
    heuristic — when autoresearch writes a `runs/<hyp_id>/commit.txt` sidecar
    we prefer that instead.
    """
    log_out = _git(
        "log", "--all", "--pretty=format:%H%x09%ct%x09%s", "--date=iso"
    )
    commits: list[tuple[str, datetime, str]] = []
    for line in log_out.splitlines():
        try:
            sha, ts, subject = line.split("\t", 2)
            commits.append((sha, datetime.fromtimestamp(int(ts)), subject))
        except ValueError:
            continue
    commits.sort(key=lambda t: t[1])

    for it in iters:
        # 1. sidecar
        sidecar = Path("runs") / it.hyp_id / "commit.txt"
        if sidecar.is_file():
            data = sidecar.read_text(encoding="utf-8").strip()
            if data:
                first, *rest = data.splitlines()
                it.commit_sha = first.strip()
                it.commit_subject = rest[0] if rest else None
                continue
        # 2. nearest preceding commit by timestamp
        target = it.produced_at
        if target.tzinfo is not None:
            target = target.replace(tzinfo=None)
        best: tuple[str, datetime, str] | None = None
        for sha, ts, subject in commits:
            if ts <= target and (best is None or ts > best[1]):
                best = (sha, ts, subject)
        if best is not None:
            it.commit_sha, it.commit_ts, it.commit_subject = best


# --------------------------------------------------------------------------- #
# classification                                                              #
# --------------------------------------------------------------------------- #


def _delta_threshold(noise_floor: dict[str, Any] | None) -> float:
    if not noise_floor:
        return _NOISE_DEFAULT_DELTA
    sigma = noise_floor.get("sigma")
    provisional = noise_floor.get("is_provisional", False)
    if provisional or sigma is None or sigma == 0.0:
        return _NOISE_DEFAULT_DELTA
    return float(2.0 * sigma)


def classify_iterations(
    iters: list[IterRecord],
    baseline_guard: dict[str, Any],
    noise_floor: dict[str, Any] | None,
) -> None:
    """Set ``accepted`` / ``delta_from_best`` / ``guard_flags`` / ``category``."""
    delta_th = _delta_threshold(noise_floor)
    running_best: float | None = None
    for it in iters:
        cer = it.corpus_cer
        if cer is None:
            it.accepted = False
            it.delta_from_best = None
        else:
            if running_best is None:
                it.accepted = True
                it.delta_from_best = None
                running_best = cer
            else:
                it.delta_from_best = cer - running_best
                if cer <= running_best - delta_th:
                    it.accepted = True
                    running_best = cer
                else:
                    it.accepted = False

        it.guard_flags = _detect_guard_flags(it.score, baseline_guard)
        it.category = _categorize(it.commit_subject)


def _detect_guard_flags(
    score: dict[str, Any], baseline_guard: dict[str, Any]
) -> list[str]:
    """Mark which guard families this iter regressed against the baseline.

    Thresholds are heuristic (PHASE2-PLAN §3.3 B is mostly diagnostic).
    """
    flags: list[str] = []
    emp = score.get("empty_output_rate") or 0.0
    if emp > 0.0:
        flags.append("empty_output")

    lr = score.get("length_ratio") or {}
    p05 = lr.get("p05")
    p95 = lr.get("p95")
    if p05 is not None and p05 < 0.10:
        flags.append("length_underrun")
    if p95 is not None and p95 > 5.0:
        flags.append("length_overrun")

    rep = score.get("repeated_text_rate") or 0.0
    base_rep = (baseline_guard.get("repeated_text_rate") or 0.0)
    if rep > base_rep + 0.10:
        flags.append("repeated_text")

    halluc = score.get("hallucination_hit_rate") or 0.0
    base_halluc = baseline_guard.get("hallucination_hit_rate") or 0.0
    if halluc > base_halluc + 0.10:
        flags.append("hallucination")

    coverage = score.get("audio_coverage_rate")
    base_coverage = baseline_guard.get("audio_coverage_rate")
    if coverage is not None and base_coverage is not None and coverage < base_coverage - 0.20:
        flags.append("coverage_low")

    return flags


def _categorize(commit_subject: str | None) -> str:
    if not commit_subject:
        return "unclassified"
    for name, regex in _CATEGORY_PATTERNS.items():
        if regex.search(commit_subject):
            return name
    return "unclassified"


# --------------------------------------------------------------------------- #
# axis builders                                                               #
# --------------------------------------------------------------------------- #


def _fmt(x: Any, places: int = 4) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{places}f}"
    return str(x)


def _learning_curve(iters: list[IterRecord]) -> str:
    if not iters:
        return "(no iterations)"
    lines = ["| iter | hyp_id | corpus_cer | accepted | running_best |",
             "|------|--------|------------|----------|--------------|"]
    best: float | None = None
    for i, it in enumerate(iters):
        if it.corpus_cer is not None and (best is None or it.corpus_cer < best):
            if it.accepted:
                best = it.corpus_cer
        lines.append(
            f"| {i + 1} | `{it.hyp_id}` | {_fmt(it.corpus_cer)} | "
            f"{'✓' if it.accepted else '·'} | {_fmt(best)} |"
        )
    return "\n".join(lines)


def _accept_timeline(iters: list[IterRecord]) -> str:
    if not iters:
        return "(empty)"
    lines = [
        "| iter | hyp_id | corpus_cer | Δ_from_prev_best | accepted | guards |",
        "|------|--------|------------|------------------|----------|--------|",
    ]
    for i, it in enumerate(iters):
        guards = ",".join(it.guard_flags) or "—"
        lines.append(
            f"| {i + 1} | `{it.hyp_id}` | {_fmt(it.corpus_cer)} | "
            f"{_fmt(it.delta_from_best)} | "
            f"{'KEEP' if it.accepted else 'REVERT'} | {guards} |"
        )
    return "\n".join(lines)


def _guard_breakdown(iters: list[IterRecord]) -> str:
    counter: Counter[str] = Counter()
    for it in iters:
        for f in it.guard_flags:
            counter[f] += 1
    if not counter:
        return "(no guard violations recorded)"
    lines = ["| guard | count |", "|------|------|"]
    for k, v in counter.most_common():
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines)


def _rollback_with_improvement(iters: list[IterRecord]) -> str:
    rows: list[str] = []
    for i, it in enumerate(iters):
        if it.accepted:
            continue
        if it.delta_from_best is not None and it.delta_from_best < 0 and it.guard_flags:
            rows.append(f"- iter {i + 1} `{it.hyp_id}` Δ={_fmt(it.delta_from_best)} guards={it.guard_flags}")
    return "\n".join(rows) or "(none)"


def _per_file_movers(iters: list[IterRecord]) -> str:
    if not iters:
        return "(no iterations)"
    first = iters[0].per_file
    last = iters[-1].per_file
    by_wav: dict[str, float] = {}
    for rec in first:
        if rec.get("cer") is not None:
            by_wav[rec["wav"]] = float(rec["cer"])
    deltas: list[tuple[str, float, float, float]] = []
    for rec in last:
        wav = rec["wav"]
        last_cer = rec.get("cer")
        first_cer = by_wav.get(wav)
        if last_cer is None or first_cer is None:
            continue
        deltas.append((wav, first_cer, float(last_cer), float(last_cer) - first_cer))
    deltas.sort(key=lambda t: t[3])
    rows = ["| wav | first_cer | last_cer | Δ |", "|------|------|------|------|"]
    for wav, fc, lc, d in deltas[:3]:
        rows.append(f"| `{Path(wav).name}` | {_fmt(fc)} | {_fmt(lc)} | {_fmt(d)} |")
    rows.append("| --- mover/stale boundary --- | | | |")
    for wav, fc, lc, d in deltas[-3:]:
        rows.append(f"| `{Path(wav).name}` | {_fmt(fc)} | {_fmt(lc)} | {_fmt(d)} |")
    return "\n".join(rows)


def _trend(
    iters: list[IterRecord],
    field_paths: Iterable[str],
    fmt_places: int = 4,
) -> str:
    paths = list(field_paths)
    head = "| iter | " + " | ".join(paths) + " |"
    sep = "|------|" + "|".join(["------"] * len(paths)) + "|"
    rows = [head, sep]
    for i, it in enumerate(iters):
        cells = []
        for p in paths:
            cells.append(_fmt(it.metric(p), fmt_places))
        rows.append(f"| {i + 1} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _diagnosis_focus_trend(iters: list[IterRecord]) -> str:
    if not iters:
        return "(no iterations)"
    counter: Counter[str] = Counter()
    rows = ["| iter | focus files |", "|------|------|"]
    for i, it in enumerate(iters):
        diag = it.diagnosis or {}
        focus = diag.get("focus_files") or []
        names = []
        for f in focus:
            name = Path(f.get("wav", "")).name
            counter[name] += 1
            names.append(name)
        rows.append(f"| {i + 1} | {', '.join(names) or '—'} |")
    rows.append("")
    rows.append("Selection frequency:")
    rows.append("")
    if counter:
        rows.append("| file | times focused |")
        rows.append("|------|------|")
        for k, v in counter.most_common():
            rows.append(f"| `{k}` | {v} |")
    return "\n".join(rows)


def _category_distribution(iters: list[IterRecord]) -> dict[str, int]:
    counter = Counter(it.category for it in iters if it.accepted)
    # Force keys present even when 0 so the template table is stable.
    for k in ("chunking", "prompt", "decode", "post"):
        counter.setdefault(k, 0)
    return dict(counter)


def _concentration_warning(distribution: dict[str, int]) -> str:
    total = sum(distribution.values())
    if total == 0:
        return "(no accepted iterations)"
    top_key = max(distribution.items(), key=lambda t: t[1])
    top_share = top_key[1] / total
    if top_share >= 0.80:
        return (
            f"⚠ {top_key[0]} 가 채택의 {top_share * 100:.0f}% — 다양성 부족 "
            "(80% 임계 초과)"
        )
    return "(within balance)"


def _cm_divergence(iters: list[IterRecord]) -> str:
    diffs: list[float] = []
    for it in iters:
        cc = it.metric("corpus_cer")
        mc = it.metric("macro_cer")
        if cc is None or mc is None:
            continue
        diffs.append(abs(cc - mc))
    if not diffs:
        return "(no data)"
    return f"mean={statistics.fmean(diffs):.4f} / max={max(diffs):.4f}"


def _per_file_dispersion(iters: list[IterRecord]) -> tuple[str, str]:
    if not iters:
        return ("n/a", "n/a")
    last = [rec.get("cer") for rec in iters[-1].per_file if rec.get("cer") is not None]
    if len(last) < 2:
        return ("n/a", "n/a")
    std = statistics.stdev(last)
    xs = sorted(last)
    q1 = xs[len(xs) // 4]
    q3 = xs[(3 * len(xs)) // 4]
    return (f"{std:.4f}", f"{q3 - q1:.4f}")


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    if dx == 0 or dy == 0:
        return None
    return num / ((dx * dy) ** 0.5)


def _guard_cer_corr(iters: list[IterRecord]) -> str:
    cers = []
    halluc = []
    for it in iters:
        if it.corpus_cer is None:
            continue
        h = it.score.get("hallucination_hit_rate")
        if h is None:
            continue
        cers.append(float(it.corpus_cer))
        halluc.append(float(h))
    corr = _pearson(cers, halluc)
    return f"pearson(corpus_cer, hallucination_hit_rate) = {_fmt(corr)}"


def _runtime_trend(iters: list[IterRecord]) -> str:
    if not iters:
        return "(no data)"
    vals = [it.metric("runtime_s_per_audio_min") for it in iters]
    vals_clean = [v for v in vals if v is not None]
    if not vals_clean:
        return "(no runtime data)"
    return (
        f"first={_fmt(vals_clean[0])} → last={_fmt(vals_clean[-1])} "
        f"(min={_fmt(min(vals_clean))}, max={_fmt(max(vals_clean))})"
    )


def _iter_durations(iters: list[IterRecord]) -> list[float]:
    deltas: list[float] = []
    for prev, curr in zip(iters, iters[1:]):
        deltas.append((curr.produced_at - prev.produced_at).total_seconds())
    return deltas


def _cost_lines(iters: list[IterRecord]) -> dict[str, str]:
    if len(iters) < 2:
        return {
            "total_wall_clock": "n/a",
            "iter_avg_s": "n/a",
            "iter_p50_s": "n/a",
            "iter_p95_s": "n/a",
            "cost_per_accept": "n/a",
            "delta_cer_per_min": "n/a",
        }
    durations = _iter_durations(iters)
    total_s = (iters[-1].produced_at - iters[0].produced_at).total_seconds()
    n_accepted = sum(1 for it in iters if it.accepted)
    initial_cer = iters[0].corpus_cer
    final_cer = iters[-1].corpus_cer
    if initial_cer is not None and final_cer is not None and total_s > 0:
        delta_per_min = (initial_cer - final_cer) / (total_s / 60.0)
    else:
        delta_per_min = None
    return {
        "total_wall_clock": f"{total_s:.0f} s",
        "iter_avg_s": f"{statistics.fmean(durations):.1f}",
        "iter_p50_s": f"{statistics.median(durations):.1f}",
        "iter_p95_s": f"{_pct(durations, 0.95):.1f}",
        "cost_per_accept": f"{total_s / n_accepted:.1f}" if n_accepted else "n/a",
        "delta_cer_per_min": _fmt(delta_per_min, 5),
    }


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    idx = q * (len(s) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _top3_share(iters: list[IterRecord]) -> tuple[str, str]:
    """Δcer Pareto for accepted iterations (improvement = positive delta)."""
    accepted = [it for it in iters if it.accepted and it.delta_from_best is not None]
    improvements = [(it, -it.delta_from_best) for it in accepted if it.delta_from_best < 0]
    if not improvements:
        return ("n/a", "(no accepted improvements)")
    total = sum(d for _, d in improvements)
    improvements.sort(key=lambda t: -t[1])
    top3 = improvements[:3]
    share = sum(d for _, d in top3) / total if total > 0 else 0.0
    lines = ["| rank | hyp_id | corpus_cer | Δ improvement |", "|------|------|------|------|"]
    for i, (it, d) in enumerate(top3, 1):
        lines.append(f"| {i} | `{it.hyp_id}` | {_fmt(it.corpus_cer)} | {_fmt(d)} |")
    return (f"{share * 100:.1f}", "\n".join(lines))


def _recovery_and_streak(iters: list[IterRecord]) -> tuple[str, str]:
    n_after_rollback = 0
    n_recovered = 0
    max_streak = 0
    current_streak = 0
    prev_rollback = False
    for it in iters:
        if it.accepted:
            if prev_rollback:
                n_recovered += 1
            current_streak = 0
            prev_rollback = False
        else:
            if prev_rollback:
                n_after_rollback += 1
            else:
                n_after_rollback += 0  # noop; counted on next accept
            current_streak += 1
            max_streak = max(max_streak, current_streak)
            prev_rollback = True
    rec_rate = (n_recovered / n_after_rollback * 100) if n_after_rollback else 0.0
    return (f"{rec_rate:.0f}" if n_after_rollback else "n/a", str(max_streak))


# --------------------------------------------------------------------------- #
# H. reasoning alignment                                                      #
# --------------------------------------------------------------------------- #


def _reasoning_alignment(iters: list[IterRecord]) -> dict[str, Any]:
    n_checked = 0
    n_aligned = 0
    mismatches: list[str] = []
    accepted = [it for it in iters if it.accepted]
    for prev, curr in zip(accepted, accepted[1:]):
        subject = (curr.commit_subject or "").lower()
        for keyword, (metric, sign) in _REASONING_RULES.items():
            if keyword.lower() not in subject:
                continue
            prev_v = prev.metric(metric)
            curr_v = curr.metric(metric)
            if prev_v is None or curr_v is None:
                continue
            n_checked += 1
            change = curr_v - prev_v
            aligned = (sign < 0 and change < 0) or (sign > 0 and change > 0)
            if aligned:
                n_aligned += 1
            else:
                mismatches.append(
                    f"- iter `{curr.hyp_id}` claimed `{keyword}` "
                    f"(expected {metric} {'↓' if sign < 0 else '↑'}), "
                    f"observed {metric}: {_fmt(prev_v)} → {_fmt(curr_v)}"
                )
    return {
        "n_checked": n_checked,
        "n_aligned": n_aligned,
        "n_misaligned": n_checked - n_aligned,
        "pct": (f"{n_aligned / n_checked * 100:.0f}" if n_checked else "n/a"),
        "mismatches": "\n".join(mismatches) or "(none)",
    }


# --------------------------------------------------------------------------- #
# orchestrator                                                                #
# --------------------------------------------------------------------------- #


def render_report(
    iters: list[IterRecord],
    target_cer_json: dict[str, Any],
    noise_floor_json: dict[str, Any] | None,
    template: str,
    job_id: str,
    holdout: dict[str, Any] | None = None,
) -> str:
    """Substitute `{{...}}` variables in ``template`` from analyzed iters."""

    baseline_guard = target_cer_json.get("guard_baseline") or {}
    classify_iterations(iters, baseline_guard, noise_floor_json)

    final = iters[-1] if iters else None
    final_cer = final.corpus_cer if final else None
    target_cer = target_cer_json.get("target_cer")
    target_reached = (
        final_cer is not None and target_cer is not None and final_cer <= target_cer
    )

    sigma = (noise_floor_json or {}).get("sigma")
    sigma_provisional = (noise_floor_json or {}).get("is_provisional", False)

    distribution = _category_distribution(iters)
    cost = _cost_lines(iters)
    top3_share_pct, top3_table = _top3_share(iters)
    recovery_pct, streak = _recovery_and_streak(iters)
    p_std, p_iqr = _per_file_dispersion(iters)
    reasoning = _reasoning_alignment(iters)

    n_accepted = sum(1 for it in iters if it.accepted)
    n_total = len(iters)
    n_rollback = n_total - n_accepted

    # Holdout placeholders are filled by evaluate_holdout.py; analyze leaves them
    # symbolic when holdout has not been run yet.
    h = holdout or {}

    substitutions: dict[str, str] = {
        "job_id": job_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_wall_clock": cost["total_wall_clock"],
        "n_iterations_seen": str(n_total),
        "final_corpus_cer": _fmt(final_cer),
        "target_cer": _fmt(target_cer),
        "baseline_cer": _fmt(target_cer_json.get("baseline_cer")),
        "target_reached": "YES" if target_reached else "NO",
        "noise_floor_sigma": _fmt(sigma, 6),
        "noise_floor_provisional": str(sigma_provisional).lower(),
        "n_accepted": str(n_accepted),
        "n_total_iter": str(n_total),
        "accept_rate_pct": f"{(n_accepted / n_total * 100):.0f}" if n_total else "n/a",
        "n_rollback": str(n_rollback),
        "learning_curve": _learning_curve(iters),
        "accept_timeline": _accept_timeline(iters),
        "eval_cer": _fmt(h.get("eval_cer")),
        "holdout_cer": _fmt(h.get("holdout_cer")),
        "holdout_delta": _fmt(h.get("delta_cer")),
        "holdout_overfit": (
            "YES" if h.get("overfit_suspected") else ("NO" if h else "n/a (not run)")
        ),
        "guard_violations_breakdown": _guard_breakdown(iters),
        "rollback_guard_violation_pct": _rollback_guard_pct(iters),
        "rollback_with_improvement": _rollback_with_improvement(iters),
        "threshold_warnings": _threshold_warnings(iters),
        "per_file_movers": _per_file_movers(iters),
        "error_breakdown_trend": _trend(
            iters,
            (
                "error_breakdown.sub_ratio",
                "error_breakdown.del_ratio",
                "error_breakdown.ins_ratio",
            ),
        ),
        "length_ratio_trend": _trend(
            iters,
            ("length_ratio.mean", "length_ratio.p05", "length_ratio.p95"),
        ),
        "halluc_rate_trend": _trend(
            iters, ("hallucination_hit_rate",), fmt_places=4
        ),
        "diagnosis_focus_trend": _diagnosis_focus_trend(iters),
        "cat_chunking": str(distribution.get("chunking", 0)),
        "cat_prompt": str(distribution.get("prompt", 0)),
        "cat_decode": str(distribution.get("decode", 0)),
        "cat_post": str(distribution.get("post", 0)),
        "cat_other": str(distribution.get("unclassified", 0)),
        "recent_5_categories": ", ".join(
            it.category for it in iters[-5:] if it.accepted
        ) or "(no accepted iters)",
        "concentration_warning": _concentration_warning(distribution),
        "cm_divergence": _cm_divergence(iters),
        "per_file_std": p_std,
        "per_file_iqr": p_iqr,
        "guard_cer_corr": _guard_cer_corr(iters),
        "runtime_trend": _runtime_trend(iters),
        "iter_avg_s": cost["iter_avg_s"],
        "iter_p50_s": cost["iter_p50_s"],
        "iter_p95_s": cost["iter_p95_s"],
        "cost_per_accept": cost["cost_per_accept"],
        "delta_cer_per_min": cost["delta_cer_per_min"],
        "top3_share_pct": top3_share_pct,
        "top3_accepted": top3_table,
        "recovery_rate_pct": recovery_pct,
        "max_rollback_streak": streak,
        "auto_alignment_pct": reasoning["pct"],
        "n_aligned": str(reasoning["n_aligned"]),
        "n_misaligned": str(reasoning["n_misaligned"]),
        "mismatch_list": reasoning["mismatches"],
    }

    out = template
    for key, val in substitutions.items():
        out = out.replace("{{" + key + "}}", val)
    return out


def _rollback_guard_pct(iters: list[IterRecord]) -> str:
    rollbacks = [it for it in iters if not it.accepted]
    if not rollbacks:
        return "n/a"
    with_guard = sum(1 for it in rollbacks if it.guard_flags)
    pct = with_guard / len(rollbacks) * 100
    return f"{pct:.0f}"


def _threshold_warnings(iters: list[IterRecord]) -> str:
    flags = Counter()
    for it in iters:
        for f in it.guard_flags:
            flags[f] += 1
    n = max(1, len(iters))
    out = []
    for k, c in flags.items():
        share = c / n
        if share >= 0.30:
            out.append(f"- `{k}` 가 {share * 100:.0f}% iter 에서 발생 — 임계 재조정 후보")
    return "\n".join(out) or "(none — guard activity within expected range)"


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 3 사후 분석 → REPORT.md")
    parser.add_argument("--runs-dir", default="runs/")
    parser.add_argument("--baseline", default="baseline/")
    parser.add_argument("--template", default="docs/templates/REPORT.md")
    parser.add_argument("--out", default="runs/_summary/REPORT.md")
    parser.add_argument("--job-id", default=None,
                        help="defaults to <runs-dir>'s last commit short sha or directory name")
    parser.add_argument(
        "--holdout-report",
        default=None,
        help="optional path to a HOLDOUT.md JSON sidecar produced by "
             "evaluate_holdout.py — when present fills the holdout section",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=__import__("os").environ.get("ASR_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    runs_dir = Path(args.runs_dir)
    baseline_dir = Path(args.baseline)
    template_path = Path(args.template)
    out_path = Path(args.out)

    target = _read_json(baseline_dir / "target_cer.json") or {}
    noise = _read_json(baseline_dir / "noise_floor.json")
    template = template_path.read_text(encoding="utf-8")

    iters = discover_iterations(runs_dir)
    if not iters:
        log.warning("no iterations discovered under %s", runs_dir)
    enrich_with_git(iters)

    holdout = _read_json(Path(args.holdout_report)) if args.holdout_report else None
    job_id = args.job_id or (_git("rev-parse", "--short", "HEAD").strip() or runs_dir.name)

    report = render_report(
        iters=iters,
        target_cer_json=target,
        noise_floor_json=noise,
        template=template,
        job_id=job_id,
        holdout=holdout,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"wrote {out_path} ({len(iters)} iterations analyzed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
