from __future__ import annotations

from slug.core import slugify


def test_lowercases_and_joins_words():
    assert slugify("Hello World") == "hello-world"


def test_collapses_runs_of_punctuation():
    assert slugify("a!!!b   c") == "a-b-c"


def test_strips_leading_and_trailing_dashes():
    assert slugify("  --Weird-- ") == "weird"


def test_non_ascii_counts_as_separator():
    assert slugify("Café Bar!") == "caf-bar"


def test_empty_and_punctuation_only_input():
    assert slugify("") == ""
    assert slugify("!!!") == ""
