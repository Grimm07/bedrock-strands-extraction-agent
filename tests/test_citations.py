"""Tests for :mod:`bedrock_strands_agent.extraction.citations`."""

from __future__ import annotations

from bedrock_strands_agent.extraction.citations import (
    value_anchored_in_excerpt,
    verify_excerpt,
)

# --------------------------------------------------------------------------- #
# value_anchored_in_excerpt — schema-confusion defence (ADR-0011)
# --------------------------------------------------------------------------- #


def test_value_anchored_in_excerpt_substring_match_passes() -> None:
    """The common case: value is contained inside excerpt."""
    assert value_anchored_in_excerpt("acme widgets", "Vendor: Acme Widgets Inc")


def test_value_anchored_in_excerpt_case_insensitive() -> None:
    """The match casefolds, mirroring `verify_excerpt`'s normalisation."""
    assert value_anchored_in_excerpt("ACME WIDGETS", "vendor: acme widgets inc")


def test_value_anchored_in_excerpt_whitespace_collapsed() -> None:
    """Internal whitespace differences must not break anchoring."""
    assert value_anchored_in_excerpt("acme   widgets", "Vendor: Acme  Widgets Inc")


def test_value_anchored_in_excerpt_fabricated_value_fails() -> None:
    """The schema-confusion case: excerpt is real, value is invented."""
    assert not value_anchored_in_excerpt("attacker@evil.com", "Vendor Name: Acme Widget Corp")


def test_value_anchored_in_excerpt_skips_non_string_values() -> None:
    """Non-string values (numbers, bools, null) are out of scope.

    They may legitimately differ from excerpt content because of validator
    normalisation (e.g. ``value=100`` extracted from ``"Total: $100.00"``).
    """
    assert value_anchored_in_excerpt(100, "Total: $100.00")
    assert value_anchored_in_excerpt(True, "Status: Yes")
    assert value_anchored_in_excerpt(None, "anything")
    assert value_anchored_in_excerpt(3.14, "pi: 3.14159")


def test_value_anchored_in_excerpt_empty_value_fails() -> None:
    assert not value_anchored_in_excerpt("", "anything")


def test_value_anchored_in_excerpt_empty_excerpt_fails() -> None:
    assert not value_anchored_in_excerpt("acme", "")
    assert not value_anchored_in_excerpt("acme", None)


def test_exact_match_is_verbatim() -> None:
    doc = "The quick brown fox jumps over the lazy dog."
    res = verify_excerpt(doc, "quick brown fox")
    assert res.verbatim is True
    assert res.overlap_pct == 100


def test_whitespace_only_difference_is_verbatim() -> None:
    doc = "The   quick    brown\nfox jumps."
    res = verify_excerpt(doc, "quick brown fox")
    assert res.verbatim is True


def test_case_only_difference_is_verbatim() -> None:
    doc = "The Quick Brown Fox."
    res = verify_excerpt(doc, "QUICK BROWN FOX")
    assert res.verbatim is True


def test_paraphrase_is_not_verbatim_and_partial_overlap() -> None:
    doc = "The quick brown fox jumps over the lazy dog."
    res = verify_excerpt(doc, "the brown fox jumped over the lazy dog")
    assert res.verbatim is False
    assert 30 < res.overlap_pct <= 100


def test_fabricated_excerpt_has_low_overlap() -> None:
    doc = "Account number 12345 is open."
    res = verify_excerpt(doc, "the secret password is taco-salad")
    assert res.verbatim is False
    assert res.overlap_pct < 30


def test_empty_excerpt_returns_not_verbatim() -> None:
    res = verify_excerpt("any document text here", "")
    assert res.verbatim is False
    assert res.overlap_pct == 0


def test_none_excerpt_returns_not_verbatim() -> None:
    res = verify_excerpt("any document text here", None)
    assert res.verbatim is False
    assert res.overlap_pct == 0


def test_overlap_pct_caps_at_100() -> None:
    """Even when the longest-match calculation rounds slightly past 100,
    we must clamp."""
    res = verify_excerpt("abcd", "abcd")
    assert res.overlap_pct == 100
