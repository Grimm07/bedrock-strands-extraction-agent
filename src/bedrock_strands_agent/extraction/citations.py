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
