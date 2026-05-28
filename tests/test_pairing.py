from pathlib import Path

import pytest

from judge.pairing import pair_batch, parse_label


def test_0715_pairs_exactly_12():
    pairs = pair_batch("AIG_녹취반출_20250715")
    assert len(pairs) == 12
    for wav, label in pairs:
        assert wav.suffix == ".wav"
        assert label.suffix == ".txt"
        assert wav.stem.endswith("_l")
        assert label.stem.endswith("_l")
        assert wav.stem == label.stem


def test_parse_label_drops_turn_numbers_and_blanks(tmp_path: Path):
    p = tmp_path / "x_l.txt"
    p.write_text(
        "1\n[INAUDIBLE] 안녕하세요\n\n2\n네\n반갑습니다\n",
        encoding="utf-8",
    )
    ref = parse_label(p)
    assert ref == "[INAUDIBLE] 안녕하세요 네 반갑습니다"


def test_pair_batch_rejects_missing_dir():
    with pytest.raises(FileNotFoundError):
        pair_batch("nonexistent_batch_xyz")
