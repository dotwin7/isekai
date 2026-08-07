from __future__ import annotations

import fnmatch


def _segments_match(segments: list[str], parts: list[str]) -> bool:
    # Dynamic programming visits each (pattern, target) prefix once. The former
    # recursive expansion retried the same suffixes exponentially for repeated
    # ``**`` segments and could stall authorization on a valid scope pattern.
    matched = [False] * (len(parts) + 1)
    matched[0] = True
    for segment in segments:
        current = [False] * (len(parts) + 1)
        if segment == "**":
            current[0] = matched[0]
            for index in range(1, len(parts) + 1):
                current[index] = matched[index] or current[index - 1]
        else:
            for index, part in enumerate(parts, start=1):
                current[index] = matched[index - 1] and fnmatch.fnmatchcase(
                    part, segment
                )
        matched = current
    return matched[-1]


def scope_pattern_matches(pattern: str, target: str) -> bool:
    """Match a project-relative scope without crossing segments accidentally."""
    normalized_pattern = pattern.replace("\\", "/")
    normalized_target = target.replace("\\", "/")
    return _segments_match(normalized_pattern.split("/"), normalized_target.split("/"))
