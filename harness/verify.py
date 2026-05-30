"""
harness/verify.py
Run candidate evaluation and Phase 3 guard checks.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness import config as cfg
from harness import guards

_BACKEND_RE = re.compile(
    r"(^|[\s])import\s+ctranslate2([\s]|$)|"
    r"(^|[\s])import\s+transformers([\s]|$)|"
    r"from\s+ctranslate2\s+import|"
    r"from\s+transformers\s+import|"
    r"importlib|__import__|from_pretrained|Whisper\(",
    re.MULTILINE,
)
_PROFILE_RE = re.compile(r"(assets|audio_profile|silero)", re.IGNORECASE)


@dataclass(frozen=True)
class VerifyConfig:
    repo_root: Path = Path(".")
    hyp_id: str = "manual"
    batch: str = "AIG_녹취반출_20250715"
    transcribe: str = "workspace.transcribe:transcribe"
    workspace_file: Path = Path("workspace/transcribe.py")
    baseline_file: Path = Path("baseline/target_cer.json")
    runs_dir: Path = Path("runs")
    runtime_hard_multiplier: float = cfg.RUNTIME_HARD_MULTIPLIER
    quality_budget_hard: bool = False
    python_executable: str = sys.executable


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    hyp_id: str
    out_dir: Path
    report: dict[str, Any] | None = None
    per_file: list[dict[str, Any]] | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


def check_workspace_static(workspace_path: Path) -> str | None:
    if not workspace_path.is_file():
        return f"workspace 파일 누락 — {workspace_path}"
    text = workspace_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(workspace_path))
    except SyntaxError as exc:
        return f"workspace syntax error: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in {"ctranslate2", "transformers"}:
                    return f"static backend: {workspace_path} 에 {alias.name} import 검출"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root in {"ctranslate2", "transformers"}:
                return f"static backend: {workspace_path} 에 from {module} import 검출"
    if _BACKEND_RE.search(text):
        return (
            f"static backend: {workspace_path} 에 "
            "ctranslate2/transformers/from_pretrained/Whisper( 패턴 검출"
        )
    if _PROFILE_RE.search(text):
        return (
            f"static profile: {workspace_path} 에 "
            "assets/audio_profile/silero 직접 참조 검출"
        )
    return None


def run_verify(config: VerifyConfig) -> VerifyResult:
    repo_root = config.repo_root.resolve()
    workspace_path = repo_root / config.workspace_file
    out_dir = repo_root / config.runs_dir / config.hyp_id
    out_dir.mkdir(parents=True, exist_ok=True)

    static_error = check_workspace_static(workspace_path)
    if static_error:
        return VerifyResult(
            ok=False,
            hyp_id=config.hyp_id,
            out_dir=out_dir,
            error=static_error,
            stderr=f"verify FAIL [{static_error}]\n",
        )

    report_path = out_dir / "score_report.json"
    per_file_path = out_dir / "per_file.jsonl"
    cmd = [
        config.python_executable,
        "-m",
        "judge.evaluate",
        "--batch",
        config.batch,
        "--transcribe",
        config.transcribe,
        "--out",
        str(report_path),
    ]

    # Wall-clock 타임아웃 = cap + 로드 여유. cap 은 사후 측정 게이트(guards.
    # check_runtime)라, candidate 가 디코딩에서 무한정 매달리면 report 가 안 나와
    # cap 체크에 도달조차 못 한다. 타임아웃이 그 hang 을 cap 근처에서 강제 종료.
    baseline = guards.read_baseline(repo_root / config.baseline_file)
    timeout_s: float | None = None
    try:
        baseline_t = float(baseline.get("total_inference_time_s", 0.0) or 0.0)
        if baseline_t > 0 and config.runtime_hard_multiplier > 0:
            cap = baseline_t * config.runtime_hard_multiplier
            timeout_s = cap + cfg.VERIFY_TIMEOUT_LOAD_MARGIN_S
    except (TypeError, ValueError):
        timeout_s = None  # baseline 이상 시 guards.check_runtime 가 별도로 reject

    try:
        run = subprocess.run(
            cmd,
            cwd=repo_root,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        partial_out = exc.stdout or ""
        partial_err = exc.stderr or ""
        if isinstance(partial_out, bytes):
            partial_out = partial_out.decode("utf-8", "replace")
        if isinstance(partial_err, bytes):
            partial_err = partial_err.decode("utf-8", "replace")
        return VerifyResult(
            ok=False,
            hyp_id=config.hyp_id,
            out_dir=out_dir,
            stdout=partial_out,
            stderr=partial_err,
            error=(
                "runtime cap 타임아웃 초과: judge.evaluate 가 "
                f"{exc.timeout:.0f}s (cap + 로드 여유) 안에 끝나지 않음 — "
                "디코딩 hang/runaway 로 강제 종료"
            ),
        )
    stdout = run.stdout
    stderr = run.stderr
    if run.returncode != 0:
        return VerifyResult(
            ok=False,
            hyp_id=config.hyp_id,
            out_dir=out_dir,
            stdout=stdout,
            stderr=stderr,
            error="judge.evaluate 종료 코드 비정상",
        )
    if not report_path.is_file() or report_path.stat().st_size == 0:
        return VerifyResult(
            ok=False,
            hyp_id=config.hyp_id,
            out_dir=out_dir,
            stdout=stdout,
            stderr=stderr,
            error="score_report.json 누락 또는 빈 파일",
        )

    report = guards.read_report(report_path)
    per_file = guards.read_per_file(per_file_path)
    guard_rc = guards.run_checks(
        report=report,
        per_file=per_file,
        baseline=baseline,
        runtime_hard_multiplier=config.runtime_hard_multiplier,
        quality_budget_hard=config.quality_budget_hard,
    )
    if guard_rc != 0:
        return VerifyResult(
            ok=False,
            hyp_id=config.hyp_id,
            out_dir=out_dir,
            report=report,
            per_file=per_file,
            stdout=stdout,
            stderr=stderr,
            error="harness guard 실패",
        )

    return VerifyResult(
        ok=True,
        hyp_id=config.hyp_id,
        out_dir=out_dir,
        report=report,
        per_file=per_file,
        stdout=stdout,
        stderr=stderr,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 3 candidate verify.")
    parser.add_argument("--hyp-id", required=True)
    parser.add_argument("--batch", default="AIG_녹취반출_20250715")
    parser.add_argument("--transcribe", default="workspace.transcribe:transcribe")
    parser.add_argument("--workspace-file", type=Path, default=Path("workspace/transcribe.py"))
    parser.add_argument("--baseline", type=Path, default=Path("baseline/target_cer.json"))
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument(
        "--runtime-hard-multiplier",
        type=float,
        default=cfg.RUNTIME_HARD_MULTIPLIER,
    )
    args = parser.parse_args(argv)

    result = run_verify(
        VerifyConfig(
            hyp_id=args.hyp_id,
            batch=args.batch,
            transcribe=args.transcribe,
            workspace_file=args.workspace_file,
            baseline_file=args.baseline,
            runs_dir=args.runs_dir,
            runtime_hard_multiplier=args.runtime_hard_multiplier,
        )
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.error:
        print(f"verify FAIL: {result.error}", file=sys.stderr)
    if result.ok and result.report:
        print(result.report["corpus_cer"])
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
