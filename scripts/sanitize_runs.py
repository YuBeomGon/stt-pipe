#!/usr/bin/env python3
"""
scripts/sanitize_runs.py
Sanitize a phase run's per-iter experiment logs into a PII-free tar.gz archive.

`runs/<hyp_id>/` is .gitignore 라 git 에 안 들어간다. 회고·재현용으로 보존하려면
고객 PII 를 제거한 아카이브로만 남긴다. 제거 대상:

  - 20자리 콜ID 파일명 (`00003004871752551193_l.wav`) → 일관 매핑 `fileNN`
  - batch 식별자 (`AIG_녹취반출_20250715`)            → `BATCH`
  - 절대경로 (`/home/<user>/.../stt-pipe/`)            → 제거 (상대경로화)
  - hallucinated_spans 의 text/pattern                 → `[REDACTED]` (방어적)

candidate.diff / candidate_meta.json / claude_std*.txt 는 candidate sandbox 산출물이라
실제 전사·콜ID 가 없지만, 치환은 전 텍스트 파일에 동일 적용(방어적)한다.

마지막에 **PII 스캐너**가 산출물을 검사해 한 건이라도 남으면 tar 를 만들지 않고 abort.
스캐너는 CER/length_ratio 같은 float 소수부(`0.16737...`)를 콜ID 로 오탐하지 않는다.

용례:
    python scripts/sanitize_runs.py --job-id phase3_005            # 스캔+아카이브
    python scripts/sanitize_runs.py --job-id phase3_005 --scan-only # tar 없이 검증만
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── 알려진 비공개 식별자 (이 repo 의 데이터 배치) ──────────────────────
BATCH_TOKENS = ["AIG_녹취반출_20250715", "AIG_녹취반출"]
HOME_PATH_RE = re.compile(r"/home/[^/\s\"']+/[^\s\"']*?stt-pipe/")
# 콜ID = 14자리 이상 연속 숫자인데, 앞이 숫자/점이 아닌 것(= float 소수부 제외).
CALLID_RE = re.compile(r"(?<![\d.])\d{14,}")
# 전화: 앞이 숫자/점이면 float 소수부 안의 우연한 일치 → 제외. 뒤에 숫자가 더
# 붙어도(더 긴 숫자열의 일부) 제외. 실제 전화는 공백/따옴표/구두점 뒤에 옴.
PHONE_RE = re.compile(r"(?<![\d.])01[016789][-_ ]?\d{3,4}[-_ ]?\d{4}(?!\d)")
# 마스킹 전화/식별자: 파일명 일부가 `010XXXX0120`, `0106613XXXX`, `0105253XXXX`
# 처럼 X 로 일부 마스킹된 콜 식별자다. 숫자에 인접한 X 런(2자 이상)을 PII 로 본다.
MASKED_RE = re.compile(r"\d[\dX]*X{2,}[\dX]*|[\dX]*X{2,}[\dX]*\d")


def _iter_dirs(runs: Path, job_id: str) -> list[Path]:
    return sorted(
        d for d in runs.glob(f"{job_id}_iter_*") if d.is_dir()
    )


def _build_callid_map(iter_dirs: list[Path]) -> dict[str, str]:
    """per_file.jsonl 의 wav basename stem 들을 모아 결정적 fileNN 매핑 생성.

    key 는 stem(`00003004871752551193_l`) 과 bare 콜ID(`00003004871752551193`) 둘 다.
    """
    stems: set[str] = set()
    for d in iter_dirs:
        pf = d / "per_file.jsonl"
        if not pf.is_file():
            continue
        for line in pf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("wav", "label"):
                val = rec.get(key)
                if not val:
                    continue
                stem = Path(str(val)).stem  # basename, no extension
                if stem:
                    stems.add(stem)
    # 결정적 순서: bare 콜ID 숫자 기준 정렬
    def _callid(stem: str) -> str:
        m = re.match(r"(\d{14,})", stem)
        return m.group(1) if m else stem

    ordered = sorted(stems, key=lambda s: (_callid(s), s))
    mapping: dict[str, str] = {}
    for i, stem in enumerate(ordered):
        alias = f"file{i:02d}"
        mapping[stem] = alias  # 00003004871752551193_l / 01_6036_010XXXX0120_... -> fileNN
        cid = _callid(stem)
        if cid != stem:
            mapping.setdefault(cid, alias)  # bare 20자리 콜ID -> fileNN
        # 마스킹폰 포맷 stem: 부분 참조(010XXXX0120 단독)도 잡히도록 파편 등록
        for frag in MASKED_RE.findall(stem):
            if frag and not frag.isdigit():  # X 를 포함한 파편만
                mapping.setdefault(frag, alias)
    return mapping


def _redact_text(text: str, callid_map: dict[str, str]) -> str:
    # 1) 절대경로 제거 (콜ID 치환 전에 — 경로 안의 콜ID 도 이후 단계서 처리)
    text = HOME_PATH_RE.sub("", text)
    # 2) 콜ID/stem 치환 — 긴 key(stem) 먼저 적용해 부분매칭 방지
    for key in sorted(callid_map, key=len, reverse=True):
        text = text.replace(key, callid_map[key])
    # 3) batch 식별자
    for tok in BATCH_TOKENS:
        text = text.replace(tok, "BATCH")
    return text


def _redact_jsonl_hallucinations(text: str) -> str:
    """per_file.jsonl 의 hallucinated_spans text/pattern 을 방어적 redact."""
    out_lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out_lines.append(line)
            continue
        try:
            rec = json.loads(s)
        except json.JSONDecodeError:
            out_lines.append(line)
            continue
        spans = rec.get("hallucinated_spans")
        if isinstance(spans, list) and spans:
            for span in spans:
                if isinstance(span, dict):
                    for k in ("text", "pattern"):
                        if k in span:
                            span[k] = "[REDACTED]"
            rec["hallucinated_spans"] = spans
            out_lines.append(json.dumps(rec, ensure_ascii=False))
        else:
            out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if text.endswith("\n") else "")


def _scan_pii(text: str) -> list[str]:
    hits: list[str] = []
    for m in CALLID_RE.finditer(text):
        hits.append(f"콜ID후보 {m.group()!r}")
    for tok in BATCH_TOKENS:
        if tok in text:
            hits.append(f"batch {tok!r}")
    for m in HOME_PATH_RE.finditer(text):
        hits.append(f"홈경로 {m.group()!r}")
    for m in PHONE_RE.finditer(text):
        hits.append(f"전화 {m.group()!r}")
    for m in MASKED_RE.finditer(text):
        if m.group() != "":
            hits.append(f"마스킹식별자 {m.group()!r}")
    return hits


def sanitize(job_id: str, runs: Path, scan_only: bool, out: Path) -> int:
    iter_dirs = _iter_dirs(runs, job_id)
    if not iter_dirs:
        print(f"sanitize: {runs}/{job_id}_iter_* 없음", file=sys.stderr)
        return 2
    callid_map = _build_callid_map(iter_dirs)
    print(f"iter dirs: {len(iter_dirs)} | 콜ID 매핑: {len(set(callid_map.values()))} 파일")

    tmp = Path(tempfile.mkdtemp(prefix="sanitize_"))
    top = tmp / job_id
    all_hits: list[str] = []
    try:
        for d in iter_dirs:
            dst = top / d.name
            dst.mkdir(parents=True, exist_ok=True)
            for src in sorted(d.rglob("*")):
                if src.is_dir():
                    continue
                rel = src.relative_to(d)
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    raw = src.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    shutil.copy2(src, target)  # binary — 그대로 (PII 없음 가정)
                    continue
                red = _redact_text(raw, callid_map)
                if src.name == "per_file.jsonl":
                    red = _redact_jsonl_hallucinations(red)
                hits = _scan_pii(red)
                if hits:
                    for h in hits[:5]:
                        all_hits.append(f"{d.name}/{rel}: {h}")
                target.write_text(red, encoding="utf-8")

        if all_hits:
            print("\n=== ❌ PII 스캔 실패 — 잔존 항목 (최대 30개 표시) ===", file=sys.stderr)
            for h in all_hits[:30]:
                print("  " + h, file=sys.stderr)
            print(f"총 {len(all_hits)} 건. tar 생성 안 함.", file=sys.stderr)
            return 1

        print("✅ PII 스캔 통과 — 콜ID/batch/홈경로/전화 0 건")
        if scan_only:
            print("(--scan-only: tar 생략)")
            return 0

        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:gz") as tar:
            tar.add(top, arcname=job_id)
        size = out.stat().st_size
        print(f"📦 아카이브: {out}  ({size:,} bytes)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sanitize phase run logs into PII-free archive.")
    p.add_argument("--job-id", required=True)
    p.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--scan-only", action="store_true")
    args = p.parse_args(argv)
    out = args.out or (
        ROOT / "docs" / "history-archive" / "runs" / f"{args.job_id}_runs_sanitized.tar.gz"
    )
    return sanitize(args.job_id, args.runs_dir, args.scan_only, out)


if __name__ == "__main__":
    raise SystemExit(main())
