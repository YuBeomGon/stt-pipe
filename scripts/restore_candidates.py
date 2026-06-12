#!/usr/bin/env python3
"""Restore candidate transcribe.py code for any past iteration, offline.

순수 로컬 연산 (LLM 호출 없음). 복원 소스 우선순위:

  1. snapshot  — iter dir 에 transcribe.py 가 통째로 저장된 경우 (simple_00x 계열)
  2. git-blob  — candidate.diff 의 `index pre..post` post hash 가 git object DB 에
                 존재하면 `git cat-file blob` 로 직접 추출
  3. blob+diff — pre hash blob (parent 코드, 커밋된 적 있어 DB 에 존재) 에
                 candidate.diff 를 git apply 로 1회 적용

검증: index line 의 post hash 와 복원 결과의 `git hash-object` 일치 여부를
MANIFEST 의 verified 컬럼에 기록한다.

사용 예:
  # 특정 후보들
  python3 scripts/restore_candidates.py --hyp phase3_030_iter_097 --hyp phase3_005_iter_098

  # CER threshold 이하 전부 (score_report 있는 모든 iter 스캔)
  python3 scripts/restore_candidates.py --max-cer 0.17

  # 특정 잡 전체
  python3 scripts/restore_candidates.py --job phase3_013 --job phase3_030

  # id 목록 파일 (한 줄에 하나)
  python3 scripts/restore_candidates.py --ids-file ids.txt

출력: --out (기본 temp/restored/) 아래 <job>/<hyp_id>/transcribe.py
      + MANIFEST.tsv (cer / source / verified / status)

phase3_004·005 는 runs 에 없고 docs/history-archive/runs/*.tar.gz 에만 있다.
해당 잡 후보가 요청되면 temp/history_runs/ 로 자동 해제 후 동일 경로로 복원한다.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"
ARCHIVE = RUNS / "_archive"
HIST_TARS = REPO / "docs" / "history-archive" / "runs"
HIST_EXTRACT = REPO / "temp" / "history_runs"
DEFAULT_OUT = REPO / "temp" / "restored"

_INDEX_RE = re.compile(r"^index ([0-9a-f]{7,40})\.\.([0-9a-f]{7,40})", re.M)


def _git(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True, **kw)


def _blob_exists(h: str) -> bool:
    return _git("cat-file", "-t", h).stdout.strip() == "blob"


def _read_blob(h: str) -> str | None:
    r = _git("cat-file", "blob", h)
    return r.stdout if r.returncode == 0 else None


def _hash_object(content: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(content)
        tmp = f.name
    try:
        return _git("hash-object", tmp).stdout.strip()
    finally:
        Path(tmp).unlink(missing_ok=True)


def _extract_history_tars() -> None:
    """docs/history-archive/runs/*.tar.gz → temp/history_runs/ (1회만)."""
    if not HIST_TARS.is_dir():
        return
    HIST_EXTRACT.mkdir(parents=True, exist_ok=True)
    for tar in sorted(HIST_TARS.glob("*.tar.gz")):
        marker = HIST_EXTRACT / f".extracted_{tar.name}"
        if marker.exists():
            continue
        print(f"[extract] {tar.name} -> {HIST_EXTRACT}")
        with tarfile.open(tar) as tf:
            tf.extractall(HIST_EXTRACT)  # noqa: S202 — 자체 생성 아카이브
        marker.touch()


def _iter_dir_candidates(hyp_id: str) -> list[Path]:
    """hyp_id 가 위치할 수 있는 iter dir 후보 경로들."""
    cands = [RUNS / hyp_id, ARCHIVE / hyp_id]
    # simple 계열: 'simple_003/0006' 또는 'simple_003_0006'
    if "/" in hyp_id:
        cands.insert(0, RUNS / Path(hyp_id))
    m = re.match(r"^(simple_\d+)[_/](\d+)$", hyp_id)
    if m:
        cands.insert(0, RUNS / m.group(1) / m.group(2))
    # history-archive 해제본 (임의 깊이에 runs/ 트리가 들어있을 수 있음)
    if HIST_EXTRACT.is_dir():
        cands += list(HIST_EXTRACT.glob(f"**/{hyp_id}"))
    return cands


def find_iter_dir(hyp_id: str) -> Path | None:
    for p in _iter_dir_candidates(hyp_id):
        if p.is_dir() and (
            (p / "candidate.diff").exists() or (p / "transcribe.py").exists()
        ):
            return p
    return None


def read_cer(iter_dir: Path) -> float | None:
    sr = iter_dir / "score_report.json"
    if not sr.exists():
        return None
    try:
        return json.loads(sr.read_text())["corpus_cer"]
    except Exception:
        return None


def _apply_diff(base: str, diff_path: Path) -> str | None:
    """base (parent transcribe.py 내용) 에 candidate.diff 적용 → 결과 내용."""
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "workspace"
        ws.mkdir()
        target = ws / "transcribe.py"
        target.write_text(base)
        r = subprocess.run(
            ["git", "apply", "--include=workspace/transcribe.py",
             str(diff_path.resolve())],
            cwd=td, capture_output=True, text=True,
        )
        if r.returncode != 0:
            # 줄끝/공백 차이 등은 완화 옵션으로 한 번 더
            r = subprocess.run(
                ["git", "apply", "--include=workspace/transcribe.py",
                 "--whitespace=nowarn", "--ignore-whitespace",
                 str(diff_path.resolve())],
                cwd=td, capture_output=True, text=True,
            )
        if r.returncode != 0:
            return None
        return target.read_text()


def restore_one(hyp_id: str) -> dict:
    """단일 후보 복원. dict(status, source, verified, cer, content) 반환."""
    out: dict = {"hyp": hyp_id, "status": "fail", "source": "",
                 "verified": "", "cer": None, "content": None, "note": ""}

    iter_dir = find_iter_dir(hyp_id)
    if iter_dir is None:
        out["note"] = "iter dir not found"
        return out
    out["cer"] = read_cer(iter_dir)

    # 1. full snapshot
    snap = iter_dir / "transcribe.py"
    if snap.exists():
        out.update(status="ok", source="snapshot", verified="n/a",
                   content=snap.read_text())
        return out

    diff_path = iter_dir / "candidate.diff"
    if not diff_path.exists():
        out["note"] = "no transcribe.py and no candidate.diff"
        return out
    diff_text = diff_path.read_text(errors="replace")
    m = _INDEX_RE.search(diff_text)
    if not m:
        out["note"] = "candidate.diff has no index line"
        return out
    pre, post = m.group(1), m.group(2)

    # 2. post blob 직접 추출
    if _blob_exists(post):
        content = _read_blob(post)
        if content is not None:
            out.update(status="ok", source="git-blob", verified="exact",
                       content=content)
            return out

    # 3. pre blob + diff 적용
    if _blob_exists(pre):
        base = _read_blob(pre)
        if base is not None:
            content = _apply_diff(base, diff_path)
            if content is None:
                out["note"] = "git apply failed on pre-blob base"
                return out
            actual = _hash_object(content)
            verified = "exact" if actual.startswith(post) else f"MISMATCH({actual[:7]}!={post})"
            out.update(status="ok", source="blob+diff", verified=verified,
                       content=content)
            return out

    out["note"] = f"neither blob in git DB (pre={pre} post={post})"
    return out


def scan_all_iter_dirs() -> list[tuple[str, Path]]:
    """알려진 모든 iter dir 을 (hyp_id, path) 로 나열."""
    found: dict[str, Path] = {}

    def add(hyp: str, p: Path) -> None:
        found.setdefault(hyp, p)

    for root in (RUNS, ARCHIVE):
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if p.is_dir() and re.match(r".+_iter_\d+$", p.name):
                add(p.name, p)
    # simple 계열: runs/simple_XXX/NNNN
    for job in sorted(RUNS.glob("simple_*")):
        if job.is_dir():
            for p in sorted(job.iterdir()):
                if p.is_dir() and re.match(r"^\d+$", p.name):
                    add(f"{job.name}/{p.name}", p)
    # history-archive 해제본
    if HIST_EXTRACT.is_dir():
        for p in sorted(HIST_EXTRACT.glob("**/*_iter_*")):
            if p.is_dir() and re.match(r".+_iter_\d+$", p.name):
                add(p.name, p)
    return sorted(found.items())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hyp", action="append", default=[],
                    help="복원할 hyp_id (반복 지정 가능)")
    ap.add_argument("--ids-file", type=Path,
                    help="hyp_id 목록 파일 (한 줄에 하나, # 주석 허용)")
    ap.add_argument("--job", action="append", default=[],
                    help="잡 전체 복원 (예: phase3_013)")
    ap.add_argument("--max-cer", type=float,
                    help="이 CER 이하의 모든 후보 복원 (전체 스캔)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"출력 루트 (기본 {DEFAULT_OUT.relative_to(REPO)})")
    ap.add_argument("--with-history-archive", action="store_true",
                    help="docs/history-archive tar.gz 해제 포함 (phase3_004/005)")
    args = ap.parse_args()

    if args.with_history_archive or any(
        j in ("phase3_004", "phase3_005") for j in args.job
    ) or any(h.startswith(("phase3_004", "phase3_005")) for h in args.hyp):
        _extract_history_tars()

    targets: list[str] = list(args.hyp)
    if args.ids_file:
        for line in args.ids_file.read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                targets.append(line)

    if args.job or args.max_cer is not None:
        for hyp, p in scan_all_iter_dirs():
            job = hyp.split("/")[0] if "/" in hyp else hyp.rsplit("_iter_", 1)[0]
            if args.job and job not in args.job:
                if args.max_cer is None:
                    continue
            if args.max_cer is not None:
                cer = read_cer(p)
                if cer is None or cer > args.max_cer:
                    continue
            elif args.job and job not in args.job:
                continue
            targets.append(hyp)

    targets = list(dict.fromkeys(targets))  # dedup, 순서 유지
    if not targets:
        ap.error("복원 대상이 없습니다. --hyp / --job / --max-cer / --ids-file 지정.")

    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "MANIFEST.tsv"
    rows = ["hyp\tcer\tsource\tverified\tstatus\tnote\tdest"]
    ok = fail = 0
    for hyp in targets:
        r = restore_one(hyp)
        dest = ""
        if r["status"] == "ok":
            job = hyp.split("/")[0] if "/" in hyp else hyp.rsplit("_iter_", 1)[0]
            d = args.out / job / hyp.replace("/", "_")
            d.mkdir(parents=True, exist_ok=True)
            (d / "transcribe.py").write_text(r["content"])
            p = d / "transcribe.py"
            dest = str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)
            ok += 1
        else:
            fail += 1
        cer = f"{r['cer']:.6f}" if r["cer"] is not None else ""
        rows.append(f"{hyp}\t{cer}\t{r['source']}\t{r['verified']}\t{r['status']}\t{r['note']}\t{dest}")
        flag = "" if r["status"] == "ok" else "  <-- FAIL"
        print(f"{hyp:<28} cer={cer:<10} {r['source']:<10} {r['verified']:<8} {r['status']}{flag} {r['note']}")

    manifest.write_text("\n".join(rows) + "\n")
    mpath = manifest.relative_to(REPO) if manifest.is_relative_to(REPO) else manifest
    print(f"\n{ok} ok / {fail} fail  ->  {mpath}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
