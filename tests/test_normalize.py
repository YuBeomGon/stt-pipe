from judge.normalize import normalize


def test_inaudible_removed_and_whitespace_stripped():
    assert normalize("[INAUDIBLE] 안녕하세요!") == "안녕하세요"
    assert normalize("[inaudible]  네 그럼요.") == "네그럼요"


def test_lowercase_and_digits_kept():
    assert normalize("  ABC123 def  ") == "abc123def"


def test_ideographic_space_stripped():
    assert normalize("가　나 다") == "가나다"


def test_punctuation_set_dropped():
    assert normalize("헬로, 월드.") == "헬로월드"
    assert normalize("「테스트」 (1)") == "테스트1"


def test_nfc_normalization():
    decomposed = "각"  # 가 + jongseong ㄱ → 각 in NFC
    assert normalize(decomposed) == "각"


def test_empty_and_none_safe():
    assert normalize("") == ""
    assert normalize(None) == ""
