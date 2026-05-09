"""Tests for :func:`bedrock_strands_agent.extraction.citations.verify_excerpt`."""

from __future__ import annotations

from bedrock_strands_agent.extraction.citations import verify_excerpt


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
