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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("analyze_run")

# Names matched verbatim against `runs/<name>/` directory entries.
_EXCLUDED_DIRNAMES = {"_summary", "_telemetry"}
# Prefixes — covers `holdout_<unix_ts>` (evaluate_holdout output), `manual_*`
# / `dry_*` (smoke / dry-run by-products), `sigma_*` (Phase 1 σ measurement
# runs), plus any future "_<bucket>" auxiliary directories. When --job-id is
# supplied discovery uses the job-id prefix instead and ignores this list,
# but the defaults still apply for the legacy no-job-id path.
_EXCLUDED_PREFIXES = ("_", "holdout_", "holdout", "manual_", "dry_", "sigma_")
_NOISE_DEFAULT_DELTA = 0.01  # absolute Δcer fallback when sigma is provisional
_EVAL_BATCH = "AIG_녹취반출_20250715"

# Category keyword groups — PHASE2-PLAN §3.3 D.
# The English keywords in PHASE2-PLAN are *examples*; the Korean equivalents
# are added because autoresearch runs against a Korean-led prompt/AGENTS/CLAUDE
# stack, so accepted commit subjects are typically Korean.
_CATEGORY_PATTERNS = {
    "chunking": re.compile(
        r"(\bchunk|\bwindow|\bsplit|\bsegment|청크|윈도우|분할|세그먼트|윈도|쪼개)",
        re.IGNORECASE,
    ),
    "prompt": re.compile(
        r"(\bprompt|\btoken|\blanguage|프롬프트|토큰|언어|힌트)",
        re.IGNORECASE,
    ),
    "decode": re.compile(
        r"(\bbeam|\btemperature|\bfallback|\bsample|빔|온도|샘플|폴백|디코딩|디코더)",
        re.IGNORECASE,
    ),
    "post": re.compile(
        r"(\bdedup|\bmerge|\bregex|\bpostprocess|\bpost-process|후처리|중복|병합|정규식)",
        re.IGNORECASE,
    ),
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
    diff_text: str = ""
    commit_sha: str | None = None
    commit_subject: str | None = None
    commit_ts: datetime | None = None
    # A' candidate metadata (proposal 2026-05-29-agent-design).
    # `candidate_lane` is one of the LANES set or None when missing.
    # `candidate_fingerprint` is a list of lowercase tokens or empty list.
    # `format_rejected` is True when candidate_meta.err exists (YAML block
    # malformed / missing / invalid). Such iters never reach verify so
    # `corpus_cer` is also None.
    candidate_lane: str | None = None
    candidate_fingerprint: list[str] = field(default_factory=list)
    format_rejected: bool = False

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


def discover_iterations(
    runs_dir: Path,
    job_id: str | None = None,
) -> list[IterRecord]:
    """Walk ``runs/`` and load every hyp_id directory with a score_report.

    Two modes:

      * **job-scoped** (``job_id`` given): only ``runs/<job_id>_iter_*/`` are
        considered. This is the contract for any Phase 3 job that follows the
        harness naming convention (``<job_id>_iter_NNN``) and is the only mode
        guaranteed to produce a single-job REPORT free of cross-job pollution.
      * **legacy** (``job_id`` is ``None``): the pre-harness behaviour kept for
        smoke tests / ad-hoc analysis — skips ``_summary`` / ``_telemetry``,
        any ``_*`` / ``holdout*`` / ``manual_*`` / ``dry_*`` / ``sigma_*``
        prefix, and any directory whose ``score_report.batch`` is not the eval
        batch.
    """
    prefix = f"{job_id}_iter_" if job_id else None
    out: list[IterRecord] = []
    for child in sorted(runs_dir.iterdir() if runs_dir.is_dir() else []):
        if not child.is_dir():
            continue
        if prefix is not None:
            if not child.name.startswith(prefix):
                continue
        else:
            if child.name in _EXCLUDED_DIRNAMES:
                continue
            if any(child.name.startswith(p) for p in _EXCLUDED_PREFIXES):
                continue
        score = _read_json(child / "score_report.json")
        if score is None:
            continue
        if score.get("batch") and score.get("batch") != _EVAL_BATCH:
            log.debug(
                "skip %s: batch=%s is not the eval batch", child.name, score.get("batch")
            )
            continue
        per_file = _read_jsonl(child / "per_file.jsonl")
        diagnosis = _read_json(child / "diagnosis_report.json")
        diff_path = child / "candidate.diff"
        diff_text = diff_path.read_text(encoding="utf-8") if diff_path.is_file() else ""
        produced_at = _parse_iso(score.get("produced_at")) or datetime.fromtimestamp(
            (child / "score_report.json").stat().st_mtime
        )
        # A' candidate metadata. Format-rejected iters never reach verify so
        # they have candidate_meta.err and no score_report — they're handled
        # separately by `count_format_rejects()`.
        meta = _read_json(child / "candidate_meta.json") or {}
        out.append(
            IterRecord(
                hyp_id=child.name,
                produced_at=produced_at,
                score=score,
                per_file=per_file,
                diagnosis=diagnosis,
                diff_text=diff_text,
                candidate_lane=meta.get("lane") if isinstance(meta.get("lane"), str) else None,
                candidate_fingerprint=meta.get("diff_fingerprint", []) if isinstance(meta.get("diff_fingerprint"), list) else [],
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
    """Attach commit metadata to each iter via subject-embedded hyp_id match.

    The harness writes commit subjects of the form ``iterN: keep|reject <hyp_id>``
    (`harness/runner.py::commit_iteration`), so each iter's commit can be
    looked up exactly by searching the log for its own ``hyp_id``. Falling back
    to nearest-timestamp matching (the previous heuristic) was off-by-one
    against ``produced_at`` because the ``score_report.produced_at`` is written
    *before* the commit lands, so ``ts <= target_unix`` picked the *previous*
    iter's commit for a sizeable fraction of cases.

    If no commit subject contains the ``hyp_id`` (e.g. iter was generated but
    never committed, or running with ``--manual``), the iter is simply left
    without commit metadata — downstream classification falls back to the
    iter's ``candidate.diff`` body.
    """
    log_out = _git(
        "log", "--all", "--pretty=format:%H%x09%ct%x09%s", "--date=iso"
    )
    by_hyp: dict[str, tuple[str, int, str]] = {}
    for line in log_out.splitlines():
        try:
            sha, ts, subject = line.split("\t", 2)
        except ValueError:
            continue
        # Match the hyp_id substring; harness format guarantees it appears in
        # the subject, and ``hyp_id`` (e.g. ``phase3_001_iter_009``) is unique
        # per iter so collisions across iters are impossible.
        for it in iters:
            if it.hyp_id in subject and it.hyp_id not in by_hyp:
                by_hyp[it.hyp_id] = (sha, int(ts), subject)

    for it in iters:
        match = by_hyp.get(it.hyp_id)
        if match is None:
            continue
        sha, ts, subject = match
        it.commit_sha = sha
        it.commit_ts = datetime.fromtimestamp(ts, tz=UTC)
        it.commit_subject = subject


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
        it.category = _categorize(it.commit_subject, it.diff_text)


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


def _categorize(commit_subject: str | None, diff_text: str = "") -> str:
    """Classify an iter into a lever family by regex match.

    The harness commit subject (``iterN: keep|reject <hyp_id>``) carries no
    semantic keywords by design — the actual lever lives in ``candidate.diff``
    (and its added comment/docstring lines). Search both: subject first so a
    rare keyword-bearing subject wins, then the diff body. The diff is
    typically a small focused change so a single category match suffices.
    """
    haystack = " ".join(filter(None, (commit_subject, diff_text)))
    if not haystack:
        return "unclassified"
    for name, regex in _CATEGORY_PATTERNS.items():
        if regex.search(haystack):
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


def _lane_distribution(iters: list[IterRecord]) -> dict[str, int]:
    """Count accepted iters per A' lane (from candidate_meta.json).

    Differs from `_category_distribution` (which is heuristic regex on
    diff_text/commit_subject) — this uses the candidate's *self-declared*
    lane from the YAML metadata block. Lanes that produced no accepted iter
    appear with count 0 so the table is stable across jobs.
    """
    counter: Counter[str] = Counter()
    for it in iters:
        if it.accepted and it.candidate_lane:
            counter[it.candidate_lane] += 1
    for lane in ("segmentation", "decoding", "prompt", "postprocess", "telemetry"):
        counter.setdefault(lane, 0)
    return dict(counter)


def _lane_entropy(iters: list[IterRecord]) -> float | None:
    """Shannon entropy (natural log) of accepted-iter lane distribution.

    Max is ln(5) ≈ 1.6094 for perfectly uniform across 5 lanes. Returned in
    nats. None when no accepted iter has a declared lane (e.g. legacy job
    without A'). The phase3_001 baseline (chunking 83% / prompt 17%) sits at
    ~0.45 — A' single-lane forcing is considered effective when this exceeds
    ~1.2 (proposal §4 판정 표).
    """
    distribution = {
        lane: c
        for lane, c in _lane_distribution(iters).items()
        if c > 0
    }
    total = sum(distribution.values())
    if total == 0:
        return None
    import math
    entropy = 0.0
    for c in distribution.values():
        p = c / total
        entropy -= p * math.log(p)
    return entropy


def _fingerprint_jaccard_mean(iters: list[IterRecord]) -> float | None:
    """Mean pairwise Jaccard *distance* (1 - similarity) over accepted iters'
    fingerprints. Higher = more diverse mechanisms across accepted iters.

    Returns None when fewer than 2 accepted iters carry fingerprints. Used
    alongside lane entropy: a job could keep lane entropy high but still
    repeat the same hyperparameter sweep within each lane — fingerprint
    distance catches that. Proposal §4 sets > 0.5 as the A' success
    threshold.
    """
    fps = [
        set(it.candidate_fingerprint)
        for it in iters
        if it.accepted and it.candidate_fingerprint
    ]
    if len(fps) < 2:
        return None
    distances: list[float] = []
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            union = fps[i] | fps[j]
            if not union:
                continue
            similarity = len(fps[i] & fps[j]) / len(union)
            distances.append(1.0 - similarity)
    if not distances:
        return None
    return statistics.fmean(distances)


def _max_fingerprint_streak(iters: list[IterRecord]) -> int:
    """Longest contiguous run of iters (any status, in produced-at order) that
    share the *exact* same fingerprint set as their immediate neighbor.

    phase3_001 had streak 5 (iter 10~14 all repeated beam/length_penalty/
    patience). Lower streak = the candidate isn't redundantly sweeping the
    same lever after rejects. Proposal §4 sets ≤ 2 as A' target.
    """
    if not iters:
        return 0
    max_run = 1
    current = 1
    prev_fp: set[str] | None = None
    for it in iters:
        fp = set(it.candidate_fingerprint)
        if prev_fp is not None and fp and fp == prev_fp:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 1
        prev_fp = fp if fp else None
    return max_run if iters and any(it.candidate_fingerprint for it in iters) else 0


def count_format_rejects(runs_dir: Path, job_id: str | None) -> int:
    """Count iters that the harness rejected for missing/malformed YAML
    metadata block (presence of `candidate_meta.err`). These iters never
    reached verify, so they're not in the `iters` list — but their count
    matters for D-axis diagnosis (proposal §4: > 40% means profile rewrite).
    """
    if not runs_dir.is_dir():
        return 0
    prefix = f"{job_id}_iter_" if job_id else None
    n = 0
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        if prefix is not None and not child.name.startswith(prefix):
            continue
        if (child / "candidate_meta.err").is_file():
            n += 1
    return n


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
    """Return (recovery_rate_pct, max_rollback_streak).

    A *rollback episode* is a contiguous run of ROLLBACK iterations bordered
    by an accept (or the start of the job). recovery_rate = (# episodes that
    end in accept) / (# total episodes). An episode that the job ends inside
    (no terminating accept) counts toward the denominator but not the
    numerator — the job failed to recover from that one.
    """
    n_episodes = 0
    n_recovered = 0
    max_streak = 0
    current_streak = 0
    prev_was_rollback = False
    for it in iters:
        if it.accepted:
            if prev_was_rollback:
                n_recovered += 1
            current_streak = 0
            prev_was_rollback = False
        else:
            if not prev_was_rollback:
                # transition from accept (or job start) into a new rollback episode
                n_episodes += 1
            current_streak += 1
            max_streak = max(max_streak, current_streak)
            prev_was_rollback = True
    if n_episodes == 0:
        return ("n/a", str(max_streak))
    rec_rate = n_recovered / n_episodes * 100
    return (f"{rec_rate:.0f}", str(max_streak))


# --------------------------------------------------------------------------- #
# H. reasoning alignment                                                      #
# --------------------------------------------------------------------------- #


def _reasoning_alignment(iters: list[IterRecord]) -> dict[str, Any]:
    n_checked = 0
    n_aligned = 0
    mismatches: list[str] = []
    accepted = [it for it in iters if it.accepted]
    for prev, curr in zip(accepted, accepted[1:]):
        # Combine subject + diff body — harness commit subjects carry no
        # keywords, so the diff (which holds the reasoning comment block) is
        # the primary signal in practice.
        haystack = " ".join(
            filter(None, (curr.commit_subject, curr.diff_text))
        ).lower()
        for keyword, (metric, sign) in _REASONING_RULES.items():
            if keyword.lower() not in haystack:
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
    state: dict[str, Any] | None = None,
    format_reject_count: int = 0,
) -> str:
    """Substitute `{{...}}` variables in ``template`` from analyzed iters.

    ``state`` is the parsed ``runs/_summary/<job_id>_state.json`` (the
    ``HarnessState`` SSOT); when present its ``best_cer`` / ``best_hyp_id``
    win over any locally-derived figure. The classifier still runs to populate
    the timeline / category / guard tables, but the headline "best" number is
    pulled from the harness so REPORT can never disagree with what the loop
    actually accepted.
    """

    baseline_guard = target_cer_json.get("guard_baseline") or {}
    classify_iterations(iters, baseline_guard, noise_floor_json)

    # Best = harness state if available, else last classified-accepted iter.
    # Never use ``iters[-1]`` blindly — a rejected last iter (extremely common
    # under the harness's reject-on-no-improvement policy) would otherwise
    # surface as the report's headline corpus_cer.
    best_iter: IterRecord | None = None
    if state and state.get("best_hyp_id"):
        best_hyp = state["best_hyp_id"]
        best_iter = next((it for it in iters if it.hyp_id == best_hyp), None)
    if best_iter is None:
        accepted_iters = [it for it in iters if it.accepted]
        best_iter = accepted_iters[-1] if accepted_iters else (iters[-1] if iters else None)

    final_cer: float | None
    if state and isinstance(state.get("best_cer"), (int, float)):
        final_cer = float(state["best_cer"])
    else:
        final_cer = best_iter.corpus_cer if best_iter else None
    target_cer = target_cer_json.get("target_cer")
    target_reached = (
        final_cer is not None and target_cer is not None and final_cer <= target_cer
    )

    sigma = (noise_floor_json or {}).get("sigma")
    sigma_provisional = (noise_floor_json or {}).get("is_provisional", False)

    distribution = _category_distribution(iters)
    lane_dist = _lane_distribution(iters)
    lane_entropy = _lane_entropy(iters)
    fp_jaccard = _fingerprint_jaccard_mean(iters)
    fp_streak = _max_fingerprint_streak(iters)
    cost = _cost_lines(iters)
    top3_share_pct, top3_table = _top3_share(iters)
    recovery_pct, streak = _recovery_and_streak(iters)
    p_std, p_iqr = _per_file_dispersion(iters)
    reasoning = _reasoning_alignment(iters)

    n_accepted = sum(1 for it in iters if it.accepted)
    n_total = len(iters)
    n_rollback = n_total - n_accepted
    n_attempted = n_total + format_reject_count
    format_reject_pct = (
        f"{(format_reject_count / n_attempted * 100):.0f}"
        if n_attempted else "n/a"
    )

    # Holdout placeholders are filled by evaluate_holdout.py; analyze leaves them
    # symbolic when holdout has not been run yet.
    h = holdout or {}

    substitutions: dict[str, str] = {
        "job_id": job_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "best_hyp_id": best_iter.hyp_id if best_iter else "n/a",
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
        # A' D-axis additions (proposal 2026-05-29-agent-design §4) — self-
        # declared lane / fingerprint metrics. lane_* metrics use the
        # candidate's own YAML metadata; cat_* above are heuristic regex on
        # commit subject + diff body and remain for legacy comparison.
        "lane_segmentation": str(lane_dist.get("segmentation", 0)),
        "lane_decoding": str(lane_dist.get("decoding", 0)),
        "lane_prompt": str(lane_dist.get("prompt", 0)),
        "lane_postprocess": str(lane_dist.get("postprocess", 0)),
        "lane_telemetry": str(lane_dist.get("telemetry", 0)),
        "lane_entropy": _fmt(lane_entropy, 3) if lane_entropy is not None else "n/a (no A' metadata)",
        "fingerprint_jaccard_mean": (
            _fmt(fp_jaccard, 3) if fp_jaccard is not None else "n/a"
        ),
        "max_fingerprint_streak": str(fp_streak),
        "format_reject_count": str(format_reject_count),
        "format_reject_pct": format_reject_pct,
        "n_attempted": str(n_attempted),
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


def _autodetect_job_id(summary_dir: Path) -> str | None:
    """Pick the unique ``<job_id>_state.json`` under ``runs/_summary/``.

    Returns the job_id when exactly one state file exists, else ``None`` —
    multiple state files mean the user has to disambiguate via ``--job-id``,
    and zero files means there's no harness job to analyze.
    """
    if not summary_dir.is_dir():
        return None
    candidates = sorted(summary_dir.glob("*_state.json"))
    if len(candidates) != 1:
        return None
    return candidates[0].name[: -len("_state.json")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 3 사후 분석 → REPORT.md")
    parser.add_argument("--runs-dir", default="runs/")
    parser.add_argument("--baseline", default="baseline/")
    parser.add_argument("--template", default="docs/templates/REPORT.md")
    parser.add_argument(
        "--out", default=None,
        help="기본: docs/reports/<job_id>_REPORT_<YYYY-MM-DD>.md "
             "(여러 잡·여러 종류 리포트가 한 디렉토리에 누적되므로 job_id 와 "
             "날짜를 파일명에 포함)",
    )
    parser.add_argument(
        "--job-id", default=None,
        help="harness job-id (prefix filter for runs/<job_id>_iter_*/ "
             "+ key for runs/_summary/<job_id>_state.json). Auto-detected "
             "from runs/_summary/*_state.json when exactly one exists.",
    )
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
    summary_dir = runs_dir / "_summary"

    target = _read_json(baseline_dir / "target_cer.json") or {}
    noise = _read_json(baseline_dir / "noise_floor.json")
    template = template_path.read_text(encoding="utf-8")

    job_id = args.job_id or _autodetect_job_id(summary_dir)
    state: dict[str, Any] | None = None
    if job_id:
        state = _read_json(summary_dir / f"{job_id}_state.json")
        if state is None:
            log.warning(
                "no state file at %s — REPORT will fall back to "
                "classifier-derived best",
                summary_dir / f"{job_id}_state.json",
            )

    iters = discover_iterations(runs_dir, job_id=job_id)
    if not iters:
        log.warning(
            "no iterations discovered under %s (job_id=%s)", runs_dir, job_id
        )
    enrich_with_git(iters)
    format_reject_count = count_format_rejects(runs_dir, job_id)

    holdout = _read_json(Path(args.holdout_report)) if args.holdout_report else None
    job_id_display = job_id or (_git("rev-parse", "--short", "HEAD").strip() or runs_dir.name)

    if args.out is not None:
        out_path = Path(args.out)
    else:
        # docs/reports/<job_id>_REPORT_<YYYY-MM-DD>.md — job·날짜·종류가 모두
        # 파일명에 들어가야 여러 잡 (phase3_001, phase3_002…) 의 REPORT 가
        # 같은 디렉토리에 누적돼도 충돌하지 않고, 같은 잡 재분석 시에도 새
        # 날짜로 분리 보존된다. HOLDOUT 은 evaluate_holdout.py 가 동일 스킴
        # 으로 옆자리에 떨군다.
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        out_path = Path("docs/reports") / f"{job_id_display}_REPORT_{date_str}.md"

    report = render_report(
        iters=iters,
        target_cer_json=target,
        noise_floor_json=noise,
        template=template,
        job_id=job_id_display,
        holdout=holdout,
        state=state,
        format_reject_count=format_reject_count,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"wrote {out_path} ({len(iters)} iterations analyzed, job_id={job_id_display})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
