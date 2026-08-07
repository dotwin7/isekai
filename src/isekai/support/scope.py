from __future__ import annotations

import fnmatch


def _segments_match(segments: list[str], parts: list[str]) -> bool:
    if not segments:
        return not parts
    head, rest = segments[0], segments[1:]
    if head == "**":
        return any(
            _segments_match(rest, parts[index:])
            for index in range(len(parts) + 1)
        )
    return (
        bool(parts)
        and fnmatch.fnmatchcase(parts[0], head)
        and _segments_match(rest, parts[1:])
    )


def scope_pattern_matches(pattern: str, target: str) -> bool:
    """Match a project-relative scope without crossing segments accidentally."""
    normalized_pattern = pattern.replace("\\", "/")
    normalized_target = target.replace("\\", "/")
    return _segments_match(normalized_pattern.split("/"), normalized_target.split("/"))
