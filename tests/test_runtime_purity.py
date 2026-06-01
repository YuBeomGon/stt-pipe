"""``verify.check_workspace_static`` runtime-purity 정적 가드 (proposal §9, Step 6).

accidental/explicit contamination guard — 파일/네트워크/동적실행·민감경로 차단.
단 정당한 사용(re.compile, frozen.load(), processor 호출)은 통과해야 한다.
"""

from __future__ import annotations

from pathlib import Path

from harness.verify import check_workspace_static

_STUB = '''\
from __future__ import annotations
import numpy as np
import re
from frozen.asr_backend import generate, load, to_storage_view

_PAT = re.compile(r"\\s+")

def transcribe(audio, sr):
    model, processor = load()
    inputs = processor(audio, sampling_rate=sr, return_tensors="np")
    feats = to_storage_view(inputs.input_features)
    out = generate(feats, [[1, 2, 3]], beam_size=5)
    return _PAT.sub(" ", processor.tokenizer.decode(out[0].sequences_ids[0]))
'''


def _check(tmp_path: Path, body: str) -> str | None:
    p = tmp_path / "transcribe.py"
    p.write_text(body, encoding="utf-8")
    return check_workspace_static(p)


def test_legit_stub_passes(tmp_path: Path) -> None:
    # frozen.load(), re.compile, processor 호출 등 정당한 코드는 통과.
    assert _check(tmp_path, _STUB) is None


def test_open_is_blocked(tmp_path: Path) -> None:
    err = _check(tmp_path, "def transcribe(a, sr):\n    open('x').read()\n    return ''\n")
    assert err and "runtime-purity" in err


def test_read_text_is_blocked(tmp_path: Path) -> None:
    err = _check(
        tmp_path,
        "from pathlib import Path\ndef transcribe(a, sr):\n"
        "    Path('y').read_text()\n    return ''\n",
    )
    assert err and "runtime-purity" in err


def test_sensitive_path_blocked(tmp_path: Path) -> None:
    err = _check(
        tmp_path,
        "def transcribe(a, sr):\n    x = 'baseline/target_cer.json'\n    return ''\n",
    )
    assert err and "runtime-purity" in err


def test_holdout_reference_blocked(tmp_path: Path) -> None:
    err = _check(tmp_path, "def transcribe(a, sr):\n    h = 'holdout'\n    return ''\n")
    assert err and "runtime-purity" in err


def test_socket_blocked(tmp_path: Path) -> None:
    err = _check(tmp_path, "import socket\ndef transcribe(a, sr):\n    return ''\n")
    assert err and "runtime-purity" in err


def test_re_compile_not_blocked(tmp_path: Path) -> None:
    # re.compile 은 정당한 postprocess → 막지 않는다 (compile( 전체 deny 안 함).
    body = (
        "import re\ndef transcribe(a, sr):\n"
        "    pat = re.compile(r'[0-9]+')\n    return pat.sub('', 'x')\n"
    )
    assert _check(tmp_path, body) is None


def test_metadata_path_not_false_positive(tmp_path: Path) -> None:
    # 'metadata/' 는 'data/' 부분일치로 오탐하면 안 된다(word boundary).
    body = "def transcribe(a, sr):\n    label = 'metadata/info'\n    return ''\n"
    assert _check(tmp_path, body) is None
