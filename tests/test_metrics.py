import math

from judge.metrics import corpus_aggregate, per_file_metrics


def test_golden_deletion_only():
    m = per_file_metrics(
        ref_norm="안녕하세요",
        hyp_norm="안녕",
        hyp_raw="안녕",
        audio_s=10.0,
        decode_s=1.0,
    )
    assert (m["sub"], m["del"], m["ins"], m["edits"]) == (0, 3, 0, 3)
    assert math.isclose(m["cer"], 0.6)


def test_corpus_cer_is_char_weighted():
    m1 = per_file_metrics("가나다", "가XX", "가XX", 5.0, 0.5)
    m2 = per_file_metrics("가나다라마", "가나다라마", "가나다라마", 10.0, 1.0)
    agg = corpus_aggregate([m1, m2])
    expected = (m1["edits"] + m2["edits"]) / (m1["ref_chars"] + m2["ref_chars"])
    assert math.isclose(agg["corpus_cer"], expected, rel_tol=1e-12)
    # macro is the arithmetic mean of per-file cers — different from corpus.
    assert math.isclose(agg["macro_cer"], (m1["cer"] + m2["cer"]) / 2)


def test_empty_output_handled():
    m = per_file_metrics("안녕", "", "", 5.0, 0.5)
    assert m["empty_output"] is True
    assert m["hyp_chars"] == 0
    assert m["edits"] == 2


def test_hallucination_pattern_hit_on_raw():
    m = per_file_metrics(
        ref_norm="네",
        hyp_norm="시청해주셔서감사합니다",
        hyp_raw="시청해주셔서 감사합니다.",
        audio_s=5.0,
        decode_s=0.5,
    )
    assert m["hallucination_hits"] >= 1
    assert m["hallucinated_spans"][0]["pattern"]


def test_empty_reference_excluded_from_corpus_cer():
    # File with a degenerate label (no usable transcript) must not inflate
    # the corpus_cer numerator or skew breakdown ratios.
    real = per_file_metrics("안녕하세요", "안녕", "안녕", 5.0, 0.5)
    empty_ref = per_file_metrics("", "환각텍스트", "환각텍스트", 5.0, 0.5)
    agg = corpus_aggregate([real, empty_ref])
    assert agg["num_files"] == 2
    assert agg["num_files_scored"] == 1
    assert math.isclose(agg["corpus_cer"], real["edits"] / real["ref_chars"])
    # Empty-ref file still affects rates that don't divide by chars.
    assert agg["empty_output_rate"] == 0.0


def test_repeated_text_detected():
    rep = "안녕하세" * 3 + "다른텍스트"
    m = per_file_metrics(
        ref_norm="네", hyp_norm=rep, hyp_raw=rep,
        audio_s=10.0, decode_s=1.0,
    )
    assert m["repeated_text"] is True
