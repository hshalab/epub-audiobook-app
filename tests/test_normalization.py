"""Unit tests for app/normalization.py."""
import pytest

from app.normalization import (
    NormalizationOptions,
    clean_junk_tokens,
    normalize_numbers,
    normalize_text,
    remove_dots_in_vietnamese_words,
)


def test_small_integer_to_words():
    assert normalize_numbers("1000") == "một nghìn"
    assert normalize_numbers("1060") == "một nghìn lẻ sáu mươi"
    assert normalize_numbers("9999") == "chín nghìn chín trăm chín mươi chín"


def test_long_integer_digit_by_digit():
    # 9 digits, pairs from right: 0 | 38 | 92 | 08 | 42
    assert normalize_numbers("038920842") == "không, ba tám, chín hai, không tám, bốn hai"


def test_currency_vnd():
    assert normalize_numbers("100.000đ") == "một trăm nghìn đồng"
    assert normalize_numbers("1.000.000 đ") == "một triệu đồng"
    assert normalize_numbers("VND 50000") == "năm mươi nghìn đồng"


def test_currency_usd():
    assert normalize_numbers("$50") == "năm mươi đô la"
    assert normalize_numbers("100 USD") == "một trăm đô la"


def test_date():
    assert normalize_numbers("01/01/2024") == "một tháng một năm hai không hai bốn"
    assert normalize_numbers("1/1/2024") == "một tháng một năm hai không hai bốn"


def test_time():
    assert normalize_numbers("14:30") == "mười bốn giờ ba mươi phút"
    assert normalize_numbers("9:00") == "chín giờ"


def test_percent():
    assert normalize_numbers("50%") == "năm mươi phần trăm"
    assert normalize_numbers("12,5%") == "mười hai phẩy năm phần trăm"


def test_decimal():
    assert normalize_numbers("1,5") == "một phẩy năm"
    assert normalize_numbers("3,14") == "ba phẩy mười bốn"
    assert normalize_numbers("100,5") == "một trăm phẩy năm"
    assert normalize_numbers("1000,001") == "một nghìn phẩy một"


def test_plain_integer_longer_than_four_digits():
    # Numbers with >= 5 digits are read digit-by-digit in pairs from right.
    assert normalize_numbers("50000") == "năm, không không, không không"
    assert normalize_numbers("100000") == "một không, không không, không không"


def test_clean_junk_tokens_default():
    assert clean_junk_tokens("OO@@") == ""
    assert clean_junk_tokens("## hello ##") == " hello "


def test_clean_junk_tokens_custom():
    assert clean_junk_tokens("fooBARbaz", ["BAR"]) == "foobaz"


def test_remove_dots_in_vietnamese_words():
    assert remove_dots_in_vietnamese_words("ch.ế.t") == "chết"
    assert remove_dots_in_vietnamese_words("t.h.ế") == "thế"


def test_remove_dots_does_not_touch_english_or_urls():
    assert remove_dots_in_vietnamese_words("example.com") == "example.com"
    assert remove_dots_in_vietnamese_words("v.v.") == "v.v."
    assert remove_dots_in_vietnamese_words("U.S.A") == "U.S.A"


def test_normalize_text_full_pipeline():
    text = "OO@@ ch.ế.t 1000 lần, còn 038920842 là số điện thoại."
    result = normalize_text(text, NormalizationOptions())
    assert "OO@@" not in result
    assert "chết" in result
    assert "một nghìn" in result
    assert "không, ba tám, chín hai, không tám, bốn hai" in result


def test_normalize_text_respects_toggles():
    text = "OO@@ ch.ế.t 1000"
    opts = NormalizationOptions(numbers=False, junk=False, spellcheck=False)
    assert normalize_text(text, opts) == text

    opts = NormalizationOptions(numbers=True, junk=False, spellcheck=False)
    assert normalize_text(text, opts) == "OO@@ ch.ế.t một nghìn"

    opts = NormalizationOptions(numbers=False, junk=True, spellcheck=False)
    assert normalize_text(text, opts) == " ch.ế.t 1000"

    opts = NormalizationOptions(numbers=False, junk=False, spellcheck=True)
    assert normalize_text(text, opts) == "OO@@ chết 1000"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
