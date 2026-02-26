"""Diff engine — extracts word-boundary-aligned substitution pairs from text diffs."""

from __future__ import annotations

import difflib


def align_to_word_boundaries(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand start/end indices to encompass complete words."""
    while start > 0 and text[start - 1] != " ":
        start -= 1
    while end < len(text) and text[end] != " ":
        end += 1
    return start, end


def extract_diff_pairs(
    injected_text: str, corrected_text: str,
) -> list[tuple[str, str]]:
    """Extract word-boundary-aligned substitution pairs.

    Compares injected_text with corrected_text and returns
    a list of (original, replacement) tuples. Empty list if
    no meaningful diffs found.
    """
    if not injected_text or not corrected_text:
        return []

    matcher = difflib.SequenceMatcher(None, injected_text, corrected_text)
    opcodes = matcher.get_opcodes()

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "replace":
            orig_start, orig_end = align_to_word_boundaries(
                injected_text, i1, i2,
            )
            corr_start, corr_end = align_to_word_boundaries(
                corrected_text, j1, j2,
            )

            original = injected_text[orig_start:orig_end].strip()
            replacement = corrected_text[corr_start:corr_end].strip()

            pair = (original, replacement)
            if original and replacement and original != replacement:
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)

    return pairs
