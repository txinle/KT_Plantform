"""Compare old, unsplit fill questions without rewriting recorded responses."""
import re
import unicodedata

from .numeric import normalize_numeric_answer


def normalize_fill_text(value, *, case_sensitive=False):
    text = unicodedata.normalize('NFKC', str(value if value is not None else ''))
    text = text.translate(str.maketrans({'−': '-', '–': '-', '∶': ':'}))
    text = re.sub(r'([+\-])\s+(?=\d)', r'\1', text)
    return ' '.join((text if case_sensitive else text.casefold()).split())


def split_legacy_response(value, count):
    if count == 1:
        return [value.strip()]
    # Explicit delimiters preserve empty slots; never shift a later answer left.
    parts = re.split(r'[;,、。]', value) if re.search(r'[;,、。]', value) else value.split()
    return [part.strip() for part in parts]


def legacy_blank_count(question_type, text, *, grouped=False):
    return len(re.findall(r'_{3,}', text)) if question_type == 'fill_blank' and not grouped else 0


def _units(text, count):
    units = re.findall(r'_{3,}\s*(kW[·•]h|kΩ|mA|mV|Ω|A|V|W|J|C|倍)?', text)
    return units if len(units) == count else [''] * count


def _same_slot(expected, observed, unit, case_sensitive):
    if not expected or not observed:
        return False
    if expected == observed:
        return True
    if unit:
        unit = normalize_fill_text(unit, case_sensitive=case_sensitive)
        aliases = [unit, '欧'] if unit == 'ω' else [unit]
        for suffix in aliases:
            if observed.endswith(suffix):
                observed = observed[:-len(suffix)].strip()
                break
    try:
        return normalize_numeric_answer(expected) == normalize_numeric_answer(observed)
    except ValueError:
        return expected == observed


def fill_answer_matches(expected, observed, *, question_text='', case_sensitive=False):
    expected = normalize_fill_text(expected, case_sensitive=case_sensitive)
    observed = normalize_fill_text(observed, case_sensitive=case_sensitive)
    expected_parts = expected.split(';')
    if question_text and len(expected_parts) > 1 and legacy_blank_count('fill_blank', question_text) != len(expected_parts):
        return bool(expected) and expected == observed
    observed_parts = split_legacy_response(observed, len(expected_parts))
    return len(expected_parts) == len(observed_parts) and all(
        _same_slot(left.strip(), right, unit, case_sensitive)
        for left, right, unit in zip(expected_parts, observed_parts, _units(question_text, len(expected_parts)))
    )
