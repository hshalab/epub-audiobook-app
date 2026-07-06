"""Text normalization for Vietnamese TTS.

Applied dynamically before TTS chunking (original chapter text stays intact).
Order of operations:
  1. Remove junk tokens (e.g. EPUB formatting artifacts like OO@@).
  2. Remove dots inside Vietnamese words (e.g. ch.ế.t -> chết).
  3. Convert numbers/currency/dates/times/percentages into spoken Vietnamese.
  4. Expand file extensions (.wav, .mp3 ...) into spoken form.
  5. Ensure each line/sentence ends with punctuation.
  6. User-defined replace rules run afterwards so users can override results.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from num2words import num2words

_VIETNAMESE_LOWER = (
    "àáảãạăắằẳẵặâấầẩẫậđéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵ"
)
_VIETNAMESE_UPPER = _VIETNAMESE_LOWER.upper()
_VIETNAMESE_LETTERS = f"a-zA-Z{_VIETNAMESE_LOWER}{_VIETNAMESE_UPPER}"
_VIETNAMESE_DIACRITICS = set(_VIETNAMESE_LOWER)

_DIGIT_WORDS = [
    "không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"
]

DEFAULT_JUNK_TOKENS = ["OO@@", "@@", "##", "**"]

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+")

# Currency: requires either a prefix symbol/code ($/₫/đ/VND/USD/EUR)
# or a suffix symbol/code.
_CURRENCY_CODES = r"VND|vnd|USD|usd|EUR|eur"
_CURRENCY_SYMBOLS = r"\$|₫|đ"
_CURRENCY_RE = re.compile(
    rf"(?<![\w])"
    rf"(?:"
    rf"(?P<prefix>{_CURRENCY_CODES}|{_CURRENCY_SYMBOLS})\s*(?P<amount1>[0-9]{{1,3}}(?:[.][0-9]{{3}})+|[0-9]+)"
    rf"|"
    rf"(?P<amount2>[0-9]{{1,3}}(?:[.][0-9]{{3}})+|[0-9]+)\s*(?P<suffix>{_CURRENCY_CODES}|{_CURRENCY_SYMBOLS})"
    rf")"
    rf"(?![\w])"
)

# Date: DD/MM/YYYY or D/M/YYYY (also accepts - as separator).
_DATE_RE = re.compile(
    r"(?<![\d/])"
    r"(?P<day>0?[1-9]|[12]\d|3[01])"
    r"(?P<sep>[-/])"
    r"(?P<month>0?[1-9]|1[0-2])"
    r"(?P=sep)"
    r"(?P<year>\d{4})"
    r"(?![\d/])"
)

# Time: HH:MM or HH:MM:SS (24h).
_TIME_RE = re.compile(
    r"(?<![\d:])"
    r"(?P<hour>[0-1]?\d|2[0-3])"
    r":"
    r"(?P<minute>[0-5]\d)"
    r"(?::[0-5]\d)?"
    r"(?![\d:])"
)

# Percentage: 50% or 12,5%.
_PERCENT_RE = re.compile(
    r"(?<![\d,.])"
    r"(?P<value>[0-9]+(?:,[0-9]+)?)"
    r"\s*%"
    r"(?![\d])"
)

# Decimal using Vietnamese comma: 1,5 or 3,14.
_DECIMAL_RE = re.compile(
    r"(?<![\d,.])"
    r"(?P<integer>\d+)"
    r","
    r"(?P<fraction>\d+)"
    r"(?![\d])"
)

# Integer with dot thousands separators: 1.000.000.
_DOT_INTEGER_RE = re.compile(
    r"(?<![\d,.])"
    r"(?P<num>\d{1,3}(?:\.\d{3})+)"
    r"(?![\d])"
)

# Plain integer.
_INTEGER_RE = re.compile(
    r"(?<![\d])"
    r"(?P<num>\d+)"
    r"(?![\d])"
)

# File extension: .wav, .mp3, .jpg etc.
# Two patterns: one with a preceding filename (needs space separator),
# one standalone (preceded by non-word).
_FILE_EXT_WITH_FILENAME_RE = re.compile(
    r"(?<=\w)"                       # preceded by word char (the filename)
    r"\."
    r"(?P<ext>[a-zA-Z][a-zA-Z0-9]{1,3})"  # extension: letter + 1-3 alnum = 2-4 total
    r"(?!\w)"                        # not followed by a word char
)
_FILE_EXT_STANDALONE_RE = re.compile(
    r"(?<![.\w])"                    # not preceded by dot or word char
    r"\."
    r"(?P<ext>[a-zA-Z][a-zA-Z0-9]{1,3})"
    r"(?!\w)"
)

@dataclass
class NormalizationOptions:
    numbers: bool = True
    junk: bool = True
    spellcheck: bool = True
    file_extensions: bool = True
    punctuation: bool = True
    junk_extra_tokens: list[str] | None = None


def _digit_to_word(d: str) -> str:
    return _DIGIT_WORDS[int(d)]


def _digits_to_words(num_str: str) -> str:
    """Read a digit string in pairs from right to left, e.g. 038920842 ->
    'không, ba tám, chín hai, không tám, bốn hai'.
    """
    groups: list[str] = []
    i = len(num_str)
    while i > 0:
        start = max(0, i - 2)
        groups.append(num_str[start:i])
        i = start
    groups.reverse()
    return ", ".join(
        " ".join(_digit_to_word(ch) for ch in group)
        for group in groups
    )


def _read_two_digits(num_str: str) -> str:
    """Read a 1-2 digit string purely as digits: 20 -> 'hai không', 24 -> 'hai tư'."""
    return " ".join(_digit_to_word(ch) for ch in num_str)


def _number_to_words(n: int | str) -> str:
    """Convert a pure integer to Vietnamese words.

    - < 5 digits: read as a whole number (e.g. 1000 -> 'một nghìn').
    - >= 5 digits: read digit-by-digit in pairs from right to left.
    Leading zeros are preserved for the digit-by-digit path.
    """
    s = str(n)
    if len(s) < 5:
        return num2words(int(s), lang="vi")
    return _digits_to_words(s)


def _year_to_words(year: int) -> str:
    """Read a year, normally as two pairs: 2024 -> 'hai không hai tư'."""
    s = str(year)
    if len(s) == 4:
        return " ".join(_read_two_digits(s[i : i + 2]) for i in range(0, 4, 2))
    return _digits_to_words(s)


def _currency_unit(match_text: str) -> str:
    text = match_text.lower()
    if "usd" in text or "$" in text:
        return "đô la"
    if "eur" in text or "€" in text:
        return "euro"
    return "đồng"


def _replace_currency(m: re.Match) -> str:
    raw = m.group(0)
    amount_str = (m.group("amount1") or m.group("amount2")).replace(".", "")
    n = int(amount_str)
    unit = _currency_unit(raw)
    # Currency always gets semantic reading regardless of digit count.
    return f"{num2words(n, lang='vi')} {unit}"


def _replace_date(m: re.Match) -> str:
    day = int(m.group("day"))
    month = int(m.group("month"))
    year = int(m.group("year"))
    day_word = num2words(day, lang="vi")
    month_word = num2words(month, lang="vi")
    year_word = _year_to_words(year)
    return f"{day_word} tháng {month_word} năm {year_word}"


def _replace_time(m: re.Match) -> str:
    hour = int(m.group("hour"))
    minute = int(m.group("minute"))
    hour_word = num2words(hour, lang="vi")
    if minute == 0:
        return f"{hour_word} giờ"
    minute_word = num2words(minute, lang="vi")
    return f"{hour_word} giờ {minute_word} phút"


def _replace_percent(m: re.Match) -> str:
    value_str = m.group("value")
    if "," in value_str:
        integer_str, fraction_str = value_str.split(",")
        value_word = (
            f"{num2words(int(integer_str), lang='vi')} phẩy "
            f"{num2words(int(fraction_str), lang='vi')}"
        )
    else:
        value_word = num2words(int(value_str), lang="vi")
    return f"{value_word} phần trăm"


def _replace_decimal(m: re.Match) -> str:
    integer_str = m.group("integer")
    fraction_str = m.group("fraction")
    return (
        f"{num2words(int(integer_str), lang='vi')} phẩy "
        f"{num2words(int(fraction_str), lang='vi')}"
    )


def _replace_dot_integer(m: re.Match) -> str:
    s = m.group("num").replace(".", "")
    return _number_to_words(s)


def _replace_integer(m: re.Match) -> str:
    return _number_to_words(m.group("num"))


def _read_extension(ext: str) -> str:
    """Read a file extension for TTS: if all letters keep as-is, else spell character-by-character."""
    if ext.isalpha():
        return ext
    words = []
    for ch in ext:
        if ch.isdigit():
            words.append(_digit_to_word(ch))
        else:
            words.append(ch)
    return " ".join(words)


def _replace_file_ext_spaced(m: re.Match) -> str:
    return f" chấm {_read_extension(m.group('ext'))}"


def _replace_file_ext_standalone(m: re.Match) -> str:
    return f"chấm {_read_extension(m.group('ext'))}"


def normalize_file_extensions(text: str) -> str:
    """Expand file extensions into spoken form (.wav -> 'chấm wav', .mp3 -> 'chấm m p ba')."""
    text = _FILE_EXT_WITH_FILENAME_RE.sub(_replace_file_ext_spaced, text)
    text = _FILE_EXT_STANDALONE_RE.sub(_replace_file_ext_standalone, text)
    return text


_SENTENCE_PUNCTUATION = frozenset(".!?…:;,)\]\"'»")


def ensure_sentence_punctuation(text: str) -> str:
    """Add a period to each line that does not already end with sentence punctuation."""
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if stripped and stripped[-1] not in _SENTENCE_PUNCTUATION:
            stripped += "."
        result.append(stripped)
    return "\n".join(result)


def _has_vietnamese_diacritic(word: str) -> bool:
    return any(ch in _VIETNAMESE_DIACRITICS for ch in word.lower())


def _remove_dots_in_word(m: re.Match) -> str:
    word = m.group(0)
    if _has_vietnamese_diacritic(word):
        return word.replace(".", "")
    return word


def remove_cjk(text: str) -> str:
    """Remove CJK Unified Ideographs from text."""
    return _CJK_RE.sub("", text)


def clean_junk_tokens(text: str, tokens: list[str] | None = None) -> str:
    """Remove known junk/formatting tokens from text."""
    tokens = tokens or DEFAULT_JUNK_TOKENS
    if not tokens:
        return text
    # Sort by length descending so longer tokens are removed first.
    sorted_tokens = sorted(set(tokens), key=len, reverse=True)
    pattern = re.compile(
        "|".join(re.escape(t) for t in sorted_tokens)
    )
    return pattern.sub("", text)


def remove_dots_in_vietnamese_words(text: str) -> str:
    """Remove dots inserted inside Vietnamese words (e.g. ch.ế.t -> chết)."""
    pattern = re.compile(
        rf"(?<!\w)"
        rf"[{_VIETNAMESE_LETTERS}]*"
        rf"(?:\.[{_VIETNAMESE_LETTERS}]+)+"
        rf"(?!\w)"
    )
    return pattern.sub(_remove_dots_in_word, text)


def normalize_numbers(text: str) -> str:
    """Convert numbers, currency, dates, times and percentages to spoken Vietnamese."""
    text = _CURRENCY_RE.sub(_replace_currency, text)
    text = _DATE_RE.sub(_replace_date, text)
    text = _TIME_RE.sub(_replace_time, text)
    text = _PERCENT_RE.sub(_replace_percent, text)
    text = _DECIMAL_RE.sub(_replace_decimal, text)
    text = _DOT_INTEGER_RE.sub(_replace_dot_integer, text)
    text = _INTEGER_RE.sub(_replace_integer, text)
    return text


def normalize_text(text: str, opts: NormalizationOptions | None = None) -> str:
    """Run the full normalization pipeline according to opts."""
    opts = opts or NormalizationOptions()
    if opts.junk:
        text = clean_junk_tokens(text, opts.junk_extra_tokens)
    text = remove_cjk(text)
    if opts.spellcheck:
        text = remove_dots_in_vietnamese_words(text)
    if opts.numbers:
        text = normalize_numbers(text)
    if opts.file_extensions:
        text = normalize_file_extensions(text)
    if opts.punctuation:
        text = ensure_sentence_punctuation(text)
    return text
