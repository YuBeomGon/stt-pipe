"""Smoke for `scripts/seal_holdout.sh` — Phase 3 §1.3 holdout 봉인.

검증:
  * 정상 시나리오: 처음 봉인 시 파일/디렉토리 모두 chmod 000 으로 봉인
  * idempotent: 이미 봉인된 상태에서 재호출해도 exit 0
  * 누락된 batch 디렉토리 → exit 1
  * `chmod -R 000` 함정 회피 — find 기반 파일/서브디렉토리 → 부모 순서
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _make_fake_holdout(root: Path, batch: str) -> tuple[Path, Path]:
    wav = root / "wav" / batch
    label = root / "label" / batch
    wav.mkdir(parents=True)
    label.mkdir(parents=True)
    # 파일 + 서브디렉토리 + 안에 또 파일 → 다단 재귀 검증
    (wav / "001.wav").write_text("fake")
    (wav / "002.wav").write_text("fake")
    sub = wav / "subdir"
    sub.mkdir()
    (sub / "nested.wav").write_text("fake")
    (label / "001.txt").write_text("text")
    (label / "002.txt").write_text("text")
    return wav, label


def _run_seal(data_root: Path, batch: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ,
           "ASR_RAW_DATA_ROOT": str(data_root),
           "BATCH": batch}
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "seal_holdout.sh")],
        env=env,
        capture_output=True,
        text=True,
    )


def _perm(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _restore(*paths: Path) -> None:
    """Restore perms so pytest's tmp_path cleanup can succeed."""
    for p in paths:
        if not p.exists():
            continue
        # walk descending so we can re-enter
        os.chmod(p, 0o700)
        for root, dirs, files in os.walk(p):
            for d in dirs:
                os.chmod(Path(root) / d, 0o700)
            for f in files:
                os.chmod(Path(root) / f, 0o600)


def test_seal_normal(tmp_path: Path) -> None:
    batch = "AIG_녹취반출_test_seal"
    wav, label = _make_fake_holdout(tmp_path, batch)

    try:
        r = _run_seal(tmp_path, batch)
        assert r.returncode == 0, r.stderr
        # 부모 디렉토리 + 안의 파일 + 서브디렉토리 모두 000
        assert _perm(wav) == 0o000
        assert _perm(label) == 0o000
        # 부모가 000 이면 ls 도 안 되므로 파일 검증은 perm 복구 후
    finally:
        _restore(wav, label)


def test_seal_idempotent(tmp_path: Path) -> None:
    """이미 봉인된 상태에서 재호출 → exit 0, already sealed 메시지."""
    batch = "AIG_녹취반출_test_idem"
    wav, label = _make_fake_holdout(tmp_path, batch)

    try:
        # 1차 봉인
        r1 = _run_seal(tmp_path, batch)
        assert r1.returncode == 0

        # 2차 호출 — 같은 명령
        r2 = _run_seal(tmp_path, batch)
        assert r2.returncode == 0, r2.stderr
        assert "already sealed" in r2.stdout
    finally:
        _restore(wav, label)


def test_seal_missing_dir(tmp_path: Path) -> None:
    """batch 디렉토리 누락 → exit 1."""
    # wav/label 디렉토리 자체 미존재
    r = _run_seal(tmp_path, "AIG_녹취반출_missing")
    assert r.returncode == 1
    assert "missing" in r.stderr


def test_seal_inner_files_become_000(tmp_path: Path) -> None:
    """find 기반이 파일·서브디렉토리·부모 순서로 chmod 했는지 확인.

    부모를 0o700 으로 복구 후 안을 들여다보면 파일들도 000 이어야 한다 — chmod
    -R 으로는 (부모 먼저 000 됨) 안의 파일이 봉인되지 못한다.
    """
    batch = "AIG_녹취반출_test_inner"
    wav, label = _make_fake_holdout(tmp_path, batch)

    try:
        r = _run_seal(tmp_path, batch)
        assert r.returncode == 0

        # 부모만 임시로 열어 안의 파일 권한 확인
        os.chmod(wav, 0o700)
        f001 = wav / "001.wav"
        f002 = wav / "002.wav"
        sub = wav / "subdir"
        assert _perm(f001) == 0o000, f"파일이 봉인 안 됨: 001.wav = {_perm(f001):o}"
        assert _perm(f002) == 0o000, f"파일이 봉인 안 됨: 002.wav = {_perm(f002):o}"
        assert _perm(sub) == 0o000, f"서브디렉토리가 봉인 안 됨: subdir = {_perm(sub):o}"

        # subdir 안의 파일도
        os.chmod(sub, 0o700)
        nested = sub / "nested.wav"
        assert _perm(nested) == 0o000, f"중첩 파일이 봉인 안 됨: nested.wav = {_perm(nested):o}"
    finally:
        _restore(wav, label)
