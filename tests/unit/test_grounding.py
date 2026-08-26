"""Direct unit tests for the shared grounding primitives -- the
adversarial cases here are what extract_facts.py, compare_subjects.py,
and validate.py all depend on to catch a fabricated value hiding behind
a real-looking citation or quote."""

from ai_daily_digest.intelligence.grounding import numbers_in, value_supported_by_quote


def test_numbers_in_strips_comma_and_currency_formatting() -> None:
    assert numbers_in("The price is $1,234.50 per month") == {"1234.50"}


def test_numbers_in_finds_every_distinct_number() -> None:
    assert numbers_in("increased from 128000 to 256000") == {"128000", "256000"}


def test_numbers_in_ignores_digits_embedded_in_a_product_name() -> None:
    """The "4" in "GPT-4o" is part of a name, not an asserted number --
    if it were picked up, a well-grounded comparison mentioning "GPT-4o"
    alongside a real number could be wrongly rejected as fabricated."""
    assert numbers_in("OpenAI's GPT-4o has a 256,000-token context window") == {"256000"}
    assert numbers_in("OpenAI's o1 model") == set()


def test_numbers_in_empty_when_no_digits() -> None:
    # Not "Apache-2.0" -- that licence string itself contains a real
    # digit run ("2.0"), which numbers_in is correct to pick up (see
    # test_value_supported_for_exact_non_numeric_phrase for how a value
    # like "Apache-2.0" is actually matched -- via phrase, not digits).
    assert numbers_in("MIT licence, no numbers here") == set()


def test_value_supported_when_number_matches_despite_comma_formatting() -> None:
    assert value_supported_by_quote("256000", "increased to 256,000 tokens") is True


def test_value_not_supported_when_number_embedded_in_a_larger_number() -> None:
    """value "20" must not be considered supported by a quote that only
    contains "120" -- a naive substring check would get this wrong."""
    assert value_supported_by_quote("20", "the price is $120 per month") is False


def test_value_not_supported_when_number_never_appears_in_quote() -> None:
    """The exact fabrication case: a real, grounded quote (it does appear
    in the source -- extract_facts.py checks that separately) that simply
    doesn't say the value the model claims it does."""
    assert value_supported_by_quote("999999", "increased to 256,000 tokens") is False


def test_value_supported_for_exact_non_numeric_phrase() -> None:
    assert value_supported_by_quote("Apache-2.0", "now licensed under Apache-2.0 terms") is True


def test_value_not_supported_for_unrelated_non_numeric_phrase() -> None:
    assert value_supported_by_quote("MIT", "now licensed under Apache-2.0 terms") is False


def test_empty_value_is_never_supported() -> None:
    assert value_supported_by_quote("", "any quote at all") is False
