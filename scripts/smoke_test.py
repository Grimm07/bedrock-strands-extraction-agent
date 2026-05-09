#!/usr/bin/env python3
"""Smoke test: verify a running instance answers /health and /schemas.

Usage:
    python scripts/smoke_test.py            # defaults to http://localhost:8000
    python scripts/smoke_test.py http://...

Exits non-zero on any failure so it can gate a deploy.
"""

from __future__ import annotations

import sys
from urllib.parse import urljoin
from urllib.request import urlopen


def check(base_url: str, path: str) -> None:
    """Hit `base_url + path` and print the result; raise on non-200."""
    url = urljoin(base_url, path)
    with urlopen(url, timeout=10) as resp:  # noqa: S310  # nosec B310
        if resp.status != 200:
            raise RuntimeError(f"{url} returned {resp.status}")
        body = resp.read().decode("utf-8")
        print(f"OK  {url}\n    {body[:200]}")


def main() -> int:
    """Run the smoke checks."""
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    try:
        check(base, "/health")
        check(base, "/schemas")
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
