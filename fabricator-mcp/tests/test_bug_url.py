"""Tests for fabricator_mcp.bug_url's prefilled GitHub issue URL builder."""
__author__ = "Adrian Melian"

import pytest

from fabricator_mcp.bug_url import REPO, build_issue_url


def test_empty_attempted_solutions_raises_value_error():
    with pytest.raises(ValueError):
        build_issue_url(
            title="t",
            description="d",
            repro_steps="r",
            attempted_solutions="",
            diagnostics={},
        )


def test_whitespace_only_attempted_solutions_raises_value_error():
    with pytest.raises(ValueError):
        build_issue_url(
            title="t",
            description="d",
            repro_steps="r",
            attempted_solutions="   \n\t  ",
            diagnostics={},
        )


def test_valid_call_builds_expected_url_shape():
    url = build_issue_url(
        title="Rig binding breaks on export",
        description="Something goes wrong during export.",
        repro_steps="1. Open scene\n2. Export",
        attempted_solutions="Tried rebuilding the binding, no change.",
        diagnostics={"maya_version": "2025"},
    )
    assert url.startswith(f"https://github.com/{REPO}/issues/new?")
    # labels param, url-encoded (comma -> %2C)
    assert "labels=bug%2Cneeds-triage" in url
    # title present, url-encoded (space -> %20)
    assert "Rig%20binding%20breaks%20on%20export" in url
    # attempted_solutions text present (plain-ASCII word segment survives
    # quote() unescaped)
    assert "rebuilding" in url
    # diagnostics value made it into the body
    assert "2025" in url


def test_huge_description_stays_under_url_limit_and_marks_truncation():
    huge = "x" * 50_000
    url = build_issue_url(
        title="t",
        description=huge,
        repro_steps="r",
        attempted_solutions="tried stuff",
        diagnostics={},
    )
    assert len(url) < 8000
    # '(truncated)' is inside the percent-encoded body, so parens are
    # escaped: '(' -> %28, ')' -> %29.
    assert "%28truncated%29" in url


def test_huge_everything_stays_under_url_limit():
    # Pathological: every section absurdly large, to exercise the
    # bisection safety net (not just the per-section pre-truncation).
    huge = "y" * 200_000
    url = build_issue_url(
        title="t" * 5_000,
        description=huge,
        repro_steps=huge,
        attempted_solutions=huge,
        diagnostics={"blob": huge},
    )
    assert len(url) < 8000
    assert url.startswith(f"https://github.com/{REPO}/issues/new?")
