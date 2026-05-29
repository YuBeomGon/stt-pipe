"""Holdout (0813) 1 회 평가 — `docs/PHASE2-PLAN.md` §4 구현.

    python -m scripts.evaluate_holdout --unseal
    python -m scripts.evaluate_holdout --dry-run

Phase 3 잡 종료 후 (`runs/_summary/JOB_DONE.lock` 존재) 한 번만 호출한다.
홀드아웃 디렉토리를 `chmod u+rwX` 로 복구하고 `judge.evaluate` 와 동일한
코드 경로로 0813 을 평가한 뒤, 결과를 0715 마지막 채택 결과와 비교해
overfit 판정과 함께 `HOLDOUT.md` + `HOLDOUT.json` 사이드카로 떨군다.
평가 후 chmod 000 으로 재봉인해 재호출을 차단한다.

`--unseal` 없이는 chmod 단계를 거부한다 (의도된 명시적 호출만).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("evaluate_holdout")

_HOLDOUT_BATCH = "AIG_녹취반출_20250813"
_EVAL_BATCH = "AIG_녹취반출_20250715"
_NOISE_DEFAULT_DELTA = 0.01


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read JSON, returning ``None`` if missing or unparseable.

    Consistent with ``analyze_run._read_json`` — a corrupt sidecar or score
    report must not crash the 1-shot holdout flow before the chmod re-seal
    can run. Unparseable input gets logged once and treated as absent.
    """
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("bad json %s: %s", path, exc)
        return None


def _resolve_data_root() -> Path:
    env = os.environ.get("ASR_RAW_DATA_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "raw"


def _holdout_paths(data_root: Path) -> tuple[Path, Path]:
    return (
        data_root / "wav" / _HOLDOUT_BATCH,
        data_root / "label" / _HOLDOUT_BATCH,
    )


def _chmod_recursive(target: Path, mode: int) -> None:
    """Apply chmod to a directory tree without following symlinks where possible."""
    # Use the same `chmod -R` semantics seal_holdout.sh relies on so behaviour
    # is symmetric. Bash is invoked rather than os.walk to keep semantics one
    # source-of-truth.
    subprocess.run(
        ["chmod", "-R", f"{mode:03o}", str(target)],
        check=True,
    )


def _is_sealed(target: Path) -> bool:
    """Heuristic: directory unreadable means sealed."""
    try:
        next(target.iterdir())
        return False
    except (PermissionError, StopIteration):
        # Either we can't read it (sealed) or it's empty (not sealed). The
        # StopIteration branch returns False because empty != sealed.
        try:
            target.iterdir()
        except PermissionError:
            return True
        return False
    except FileNotFoundError:
        return False


def _best_eval_run(runs_dir: Path, summary_dir: Path) -> Path | None:
    """Resolve the anchor eval (0715) run for holdout comparison.

    Priority:
      1. ``runs/_summary/<job_id>_state.json::best_hyp_id`` — the harness's
         authoritative pointer to the iter whose ``transcribe.py`` is currently
         on disk. This is what holdout *actually* evaluates against, so it's
         the only correct anchor. Auto-detected when exactly one
         ``*_state.json`` exists; on ties / none, falls back below.
      2. Last *produced* eval-batch ``score_report`` (the pre-fix behavior) —
         kept as a defensive fallback so smoke tests that drop synthetic runs
         without a state file still work.
    """
    state_candidates = sorted(summary_dir.glob("*_state.json")) if summary_dir.is_dir() else []
    if len(state_candidates) == 1:
        state_path = state_candidates[0]
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = None
        if state and state.get("best_hyp_id"):
            best_dir = runs_dir / state["best_hyp_id"]
            if (best_dir / "score_report.json").is_file():
                return best_dir
            log.warning(
                "state %s points at %s but no score_report there — falling back to last-produced eval",
                state_path, best_dir,
            )

    candidates: list[tuple[float, Path]] = []
    for child in runs_dir.iterdir() if runs_dir.is_dir() else []:
        if not child.is_dir() or child.name.startswith("_") or child.name.startswith("holdout"):
            continue
        score = child / "score_report.json"
        if not score.is_file():
            continue
        try:
            data = json.loads(score.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("batch") != _EVAL_BATCH:
            continue
        produced_at = data.get("produced_at")
        ts = 0.0
        if produced_at:
            try:
                ts = datetime.fromisoformat(
                    produced_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                ts = score.stat().st_mtime
        else:
            ts = score.stat().st_mtime
        candidates.append((ts, child))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


# Backwards-compatible alias for existing tests; new code should use
# ``_best_eval_run`` which is state-aware.
_last_accepted_eval_run = _best_eval_run


def _per_file_table(
    eval_per_file: list[dict[str, Any]],
    holdout_per_file: list[dict[str, Any]],
) -> str:
    rows = ["| eval `corpus_cer` (per file) | holdout `corpus_cer` (per file) |",
            "|------|------|"]
    # Show separate columns since holdout/eval have *different* wavs.
    n = max(len(eval_per_file), len(holdout_per_file))
    for i in range(n):
        e = eval_per_file[i] if i < len(eval_per_file) else None
        h = holdout_per_file[i] if i < len(holdout_per_file) else None
        e_str = (
            f"`{Path(e['wav']).name}` cer={e.get('cer'):.4f}"
            if e and e.get("cer") is not None
            else "—"
        )
        h_str = (
            f"`{Path(h['wav']).name}` cer={h.get('cer'):.4f}"
            if h and h.get("cer") is not None
            else "—"
        )
        rows.append(f"| {e_str} | {h_str} |")
    return "\n".join(rows)


def _guard_diff(
    eval_score: dict[str, Any], holdout_score: dict[str, Any]
) -> str:
    fields = (
        "empty_output_rate",
        "repeated_text_rate",
        "hallucination_hit_rate",
        "audio_coverage_rate",
    )
    rows = ["| guard | eval (0715) | holdout (0813) | Δ |", "|------|------|------|------|"]
    for f in fields:
        e = eval_score.get(f)
        h = holdout_score.get(f)
        delta = (
            f"{(h - e):+.4f}"
            if isinstance(e, (int, float)) and isinstance(h, (int, float))
            else "n/a"
        )
        rows.append(
            f"| {f} | {e if e is None else f'{e:.4f}'} "
            f"| {h if h is None else f'{h:.4f}'} | {delta} |"
        )
    return "\n".join(rows)


def _write_holdout_report(
    out_dir: Path,
    eval_run_dir: Path,
    holdout_run_dir: Path,
    eval_score: dict[str, Any],
    holdout_score: dict[str, Any],
    sigma: float,
    sigma_provisional: bool,
) -> dict[str, Any]:
    eval_cer = eval_score.get("corpus_cer")
    holdout_cer = holdout_score.get("corpus_cer")
    delta_cer = (
        (holdout_cer - eval_cer)
        if isinstance(eval_cer, (int, float)) and isinstance(holdout_cer, (int, float))
        else None
    )
    threshold = 2.0 * sigma if sigma > 0 and not sigma_provisional else _NOISE_DEFAULT_DELTA
    overfit_suspected = (
        delta_cer is not None and abs(delta_cer) > threshold
    )

    eval_per = []
    pf_path = eval_run_dir / "per_file.jsonl"
    if pf_path.is_file():
        eval_per = [json.loads(line) for line in pf_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    holdout_per = []
    pf_path_h = holdout_run_dir / "per_file.jsonl"
    if pf_path_h.is_file():
        holdout_per = [json.loads(line) for line in pf_path_h.read_text(encoding="utf-8").splitlines() if line.strip()]

    md = [
        f"# Holdout 평가 — {holdout_run_dir.name}",
        "",
        f"생성 시각: {datetime.now(UTC).isoformat()}",
        f"기준 eval run: `{eval_run_dir.name}`",
        f"노이즈 floor σ: {sigma:.6f} (`is_provisional={str(sigma_provisional).lower()}`)",
        f"임계 (`|Δ| > {threshold:.4f}` 이면 overfit 의심)",
        "",
        "## Corpus 비교",
        "",
        "| 항목 | 값 |",
        "|------|------|",
        f"| eval (0715) `corpus_cer` | {eval_cer if eval_cer is None else f'{eval_cer:.6f}'} |",
        f"| holdout (0813) `corpus_cer` | {holdout_cer if holdout_cer is None else f'{holdout_cer:.6f}'} |",
        f"| Δ (`holdout - eval`) | {delta_cer if delta_cer is None else f'{delta_cer:+.6f}'} |",
        f"| Overfit 의심 | {'YES' if overfit_suspected else 'NO'} |",
        "",
        "## Guard 비교",
        "",
        _guard_diff(eval_score, holdout_score),
        "",
        "## Per-file (참고)",
        "",
        "> eval 과 holdout 은 *다른 파일 집합* 이라 직접 매칭이 아니다 — 분포 비교용.",
        "",
        _per_file_table(eval_per, holdout_per),
        "",
        "## 해석",
        "",
        "<!-- TODO: |Δcer| 가 σ 임계보다 작으면 일반화 OK. 임계 초과면 0715 overfit 의심 → "
        "0715 에서 도입된 특정 후처리/패턴 매칭이 0813 에 일반화 안 되는지 검토. -->",
        "",
    ]
    md_path = out_dir / "HOLDOUT.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    sidecar: dict[str, Any] = {
        "eval_run": eval_run_dir.name,
        "holdout_run": holdout_run_dir.name,
        "eval_cer": eval_cer,
        "holdout_cer": holdout_cer,
        "delta_cer": delta_cer,
        "sigma": sigma,
        "sigma_provisional": sigma_provisional,
        "threshold": threshold,
        "overfit_suspected": overfit_suspected,
        "produced_at": datetime.now(UTC).isoformat(),
    }
    (out_dir / "HOLDOUT.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return sidecar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Holdout (0813) 1회 평가")
    parser.add_argument("--unseal", action="store_true",
                        help="chmod -R u+rwX → evaluate → chmod -R 000 재봉인")
    parser.add_argument("--dry-run", action="store_true",
                        help="chmod 없이 코드 경로만 smoke (잠금/존재 확인 + report 골격)")
    parser.add_argument("--runs-dir", default="runs/")
    parser.add_argument("--baseline", default="baseline/")
    parser.add_argument(
        "--summary-dir", default="runs/_summary/",
        help="JOB_DONE.lock 위치이자 HOLDOUT.md 출력 디렉토리",
    )
    parser.add_argument("--out-run-id", default=None,
                        help="holdout 결과 디렉토리 이름 (기본: holdout_<unix_ts>)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("ASR_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.unseal and args.dry_run:
        parser.error("--unseal 와 --dry-run 동시 사용 금지")

    runs_dir = Path(args.runs_dir)
    summary_dir = Path(args.summary_dir)
    baseline_dir = Path(args.baseline)
    lock = summary_dir / "JOB_DONE.lock"

    # 1. lock check — Phase 3 잡 종료 마커가 있어야 진행
    if not lock.is_file():
        print(f"refused: {lock} 가 없음 — 잡 종료 후에만 호출 가능 "
              "(touch 로 명시적 마커 필요)", file=sys.stderr)
        return 2

    data_root = _resolve_data_root()
    wav_dir, label_dir = _holdout_paths(data_root)
    if not wav_dir.parent.is_dir() or not label_dir.parent.is_dir():
        print(f"refused: data root {data_root} 가 비정상 (wav/label 없음)", file=sys.stderr)
        return 3

    target = _read_json(baseline_dir / "target_cer.json") or {}
    noise = _read_json(baseline_dir / "noise_floor.json") or {}
    sigma = float(noise.get("sigma") or 0.0)
    sigma_provisional = bool(noise.get("is_provisional"))

    out_run_id = args.out_run_id or f"holdout_{int(time.time())}"
    holdout_run_dir = runs_dir / out_run_id
    holdout_run_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        log.info("dry-run: skipping chmod and evaluate; verifying glue only")
        eval_run = _best_eval_run(runs_dir, summary_dir)
        print("dry-run ok — would evaluate %s and compare against %s" %
              (wav_dir, eval_run.name if eval_run else "<no eval run>"))
        return 0

    if not args.unseal:
        print("refused: chmod 복구는 --unseal 명시 필요 — PHASE3-PLAN §8", file=sys.stderr)
        return 4

    # 2-5. unseal → evaluate → report → 재봉인.
    # chmod 복구는 try 블록 *안* 에서 수행한다. 그래야 wav unseal 성공 후 label
    # unseal 실패 같은 케이스에서도 finally 가 두 디렉토리를 무조건 다시 봉인
    # 한다 — PHASE2-PLAN §8 안티패턴 "chmod 복구만 하고 재봉인 안 하기" 방지.
    try:
        if wav_dir.exists():
            _chmod_recursive(wav_dir, 0o755)
        if label_dir.exists():
            _chmod_recursive(label_dir, 0o755)
        log.info("holdout dirs unsealed: %s, %s", wav_dir, label_dir)

        # 3. evaluate (judge.evaluate 동일 경로)
        from judge.evaluate import evaluate_batch  # local import — avoids loading GPU stack on smoke
        eval_run = _best_eval_run(runs_dir, summary_dir)
        if eval_run is None:
            log.warning("no eval (0715) run found to compare against — holdout report will be partial")
            eval_score: dict[str, Any] = {}
        else:
            eval_score = _read_json(eval_run / "score_report.json") or {}

        holdout_score = evaluate_batch(
            batch=_HOLDOUT_BATCH,
            transcribe_spec="workspace.transcribe:transcribe",
            out_path=holdout_run_dir / "score_report.json",
        )
        log.info("holdout corpus_cer = %s", holdout_score.get("corpus_cer"))

        # 4. report
        summary_dir.mkdir(parents=True, exist_ok=True)
        sidecar = _write_holdout_report(
            out_dir=summary_dir,
            eval_run_dir=eval_run if eval_run is not None else holdout_run_dir,
            holdout_run_dir=holdout_run_dir,
            eval_score=eval_score,
            holdout_score=holdout_score,
            sigma=sigma,
            sigma_provisional=sigma_provisional,
        )
        log.info("wrote %s and %s", summary_dir / "HOLDOUT.md", summary_dir / "HOLDOUT.json")

    finally:
        # 5. 재봉인 — 평가 후 chmod 000 으로 다시 잠가 재호출 차단.
        # chmod 가 한 번 실패하더라도 다른 한 쪽은 시도하도록 분리.
        for target in (wav_dir, label_dir):
            if not target.exists():
                continue
            try:
                _chmod_recursive(target, 0o000)
            except (subprocess.CalledProcessError, OSError) as exc:
                log.error("re-seal failed on %s: %s", target, exc)
        log.info("holdout dirs re-sealed (chmod 000)")

    print(f"holdout evaluation complete — see {summary_dir / 'HOLDOUT.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
