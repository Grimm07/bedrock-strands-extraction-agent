"""Verify that the model's ``source_excerpt`` actually appears in the document.

A verbatim match (whitespace + casefold normalised) returns
``CitationCheck(verbatim=True, overlap_pct=100)``. Otherwise we use
:mod:`difflib` to compute the longest contiguous match's length relative to
the excerpt length. The retry prompt (Phase C2) surfaces the percentage so
the model can re-quote rather than fabricate.

Pure stdlib — no new dependency.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Final

_WHITESPACE_RE: Final = re.compile(r"\s+")


@dataclass(frozen=True)
class CitationCheck:
    """Outcome of verifying ``excerpt`` against ``doc``."""

    verbatim: bool
    overlap_pct: int  # 0..100 inclusive


def _normalize(text: str) -> str:
    """Casefold + collapse whitespace so trivial deltas don't fail verbatim."""
    return _WHITESPACE_RE.sub(" ", text.strip().casefold())


def verify_excerpt(doc: str, excerpt: str | None) -> CitationCheck:
    """Return whether ``excerpt`` appears verbatim in ``doc`` (after normalisation).

    When the excerpt is missing or empty, returns ``CitationCheck(False, 0)``.
    When the normalised excerpt appears as a substring of the normalised
    document, returns ``CitationCheck(True, 100)``. Otherwise computes the
    longest contiguous matching substring's length relative to the excerpt
    length to give a best-overlap percentage in ``[0, 100]``.
    """
    if not excerpt:
        return CitationCheck(verbatim=False, overlap_pct=0)
    nd = _normalize(doc)
    ne = _normalize(excerpt)
    if not ne:
        return CitationCheck(verbatim=False, overlap_pct=0)
    if ne in nd:
        return CitationCheck(verbatim=True, overlap_pct=100)
    matcher = difflib.SequenceMatcher(a=nd, b=ne, autojunk=False)
    match = matcher.find_longest_match(0, len(nd), 0, len(ne))
    overlap = round(match.size / len(ne) * 100)
    return CitationCheck(verbatim=False, overlap_pct=min(100, max(0, overlap)))


def value_anchored_in_excerpt(raw_value: object, excerpt: str | None) -> bool:
    """Return whether ``raw_value`` is contained inside ``excerpt`` (normalised).

    Closes the schema-confusion gap flagged in ADR-0011: a model can return
    a verbatim ``source_excerpt`` (passes :func:`verify_excerpt`) but invent
    a ``value`` that is nowhere inside that excerpt or the wider document.
    Pairing :func:`verify_excerpt` with this anchor check forces the model
    to make the relationship between value and excerpt observable.

    Only string-valued ``raw_value`` is checked; non-string values
    (numbers, booleans, null) skip this check (return ``True``) because
    they are legitimately normalised forms of excerpt content (e.g.
    ``value=100`` extracted from ``"Total: $100.00"``,
    ``value=True`` from ``"Yes"``). Numeric/boolean fabrication is
    constrained by schema-type validation and the retry loop's required-
    field checks.

    Empty values and empty excerpts return ``False``.
    """
    if not isinstance(raw_value, str):
        return True
    if not raw_value or not excerpt:
        return False
    return _normalize(raw_value) in _normalize(excerpt)
