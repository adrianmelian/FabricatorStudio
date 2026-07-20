"""Builds a prefilled GitHub "New issue" URL for the KinematicSolutions
repo. Pure stdlib — no mcp import here, so this module can be unit tested
(and reused) without the mcp package installed.

The server never opens this URL or posts anything on the user's behalf:
report_bug hands the AI client a link, and the human reviews/submits it
under their own GitHub account. This module also enforces the anti-spam
gate: attempted_solutions is required, so an AI can't file bugs it hasn't
first tried to fix or at least ruled out fixing.
"""
__author__ = "Adrian Melian"

import json
from urllib.parse import quote, urlencode

REPO = "adrianmelian/KinematicSolutions"

_ISSUES_URL = f"https://github.com/{REPO}/issues/new"

# Hard ceiling on the final URL's length. Chosen comfortably under common
# browser/server URL-length limits (most cap out around 8k).
_MAX_URL_LEN = 7999

_TRUNCATION_MARKER = "\n...(truncated)"

# Generous per-section caps applied before we ever build the URL. These
# keep the *raw* (pre-percent-encoding) body small enough that, for any
# normal text, the encoded URL comfortably clears _MAX_URL_LEN. The
# bisection loop in build_issue_url() is the actual guarantee for
# pathological inputs (e.g. text that's mostly percent-escaped).
_TITLE_LIMIT = 200
_SECTION_LIMIT = 1500

_ATTEMPTED_SOLUTIONS_REQUIRED_MESSAGE = (
    "Propose and attempt a fix first before filing a bug: describe what "
    "you tried in attempted_solutions. If nothing applies, say so "
    "explicitly."
)


def _truncate(text: str | None, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    keep = max(limit - len(_TRUNCATION_MARKER), 0)
    return text[:keep] + _TRUNCATION_MARKER


def _build_body(description: str, repro_steps: str, attempted_solutions: str,
                 diagnostics_text: str) -> str:
    return (
        "## Description\n"
        f"{description}\n\n"
        "## Repro steps\n"
        f"{repro_steps}\n\n"
        "## Attempted solutions\n"
        f"{attempted_solutions}\n\n"
        "## Diagnostics\n"
        f"```\n{diagnostics_text}\n```\n"
    )


def build_issue_url(*, title: str, description: str, repro_steps: str,
                     attempted_solutions: str, diagnostics: dict) -> str:
    """Build a `github.com/.../issues/new?...` URL prefilled with the given
    report, for the human to review and submit themselves.

    Raises:
        ValueError: attempted_solutions is empty/whitespace-only. This is
            a deliberate anti-spam gate: an AI must try (or explicitly rule
            out) a fix before it's allowed to hand the user a bug-report
            link.
    """
    if not attempted_solutions or not attempted_solutions.strip():
        raise ValueError(_ATTEMPTED_SOLUTIONS_REQUIRED_MESSAGE)

    title_t = _truncate(title or "(untitled)", _TITLE_LIMIT)
    description_t = _truncate(description, _SECTION_LIMIT)
    repro_t = _truncate(repro_steps, _SECTION_LIMIT)
    attempted_t = _truncate(attempted_solutions, _SECTION_LIMIT)
    diagnostics_text = json.dumps(diagnostics or {}, indent=2, default=str)
    diagnostics_t = _truncate(diagnostics_text, _SECTION_LIMIT)

    body = _build_body(description_t, repro_t, attempted_t, diagnostics_t)

    def _make_url(body_text: str) -> str:
        query = urlencode(
            {
                "title": title_t,
                "labels": "bug,needs-triage",
                "body": body_text,
            },
            quote_via=quote,
        )
        return f"{_ISSUES_URL}?{query}"

    url = _make_url(body)

    # Safety net: even the generous per-section caps above can, for
    # pathological inputs (text that's almost entirely percent-escaped),
    # still push the URL past the hard cap. Bisect the raw body text down
    # until the encoded URL fits. This always terminates: `body` shrinks
    # by roughly half each iteration until it hits `floor`.
    floor = 200
    while len(url) >= _MAX_URL_LEN and len(body) > floor:
        keep = max(len(body) // 2, floor)
        body = body[:keep] + _TRUNCATION_MARKER
        url = _make_url(body)

    return url
