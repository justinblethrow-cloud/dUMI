#!/usr/bin/env python3
"""Fail when a Markdown or HTML link points to a missing archive-local target."""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.parse
from pathlib import Path


INLINE_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+]:\s*(<[^>]+>|\S+)")
HTML_LINK = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="root of the extracted release tree")
    return parser.parse_args()


def destination(raw_destination: str) -> str:
    value = html.unescape(raw_destination.strip())
    if value.startswith("<"):
        closing = value.find(">")
        return value[1:closing] if closing >= 0 else value[1:]
    return value.split(maxsplit=1)[0] if value else ""


def local_target(root: Path, markdown_file: Path, target: str) -> Path | None:
    target = urllib.parse.unquote(target)
    if (
        not target
        or target.startswith("#")
        or target.startswith("//")
        or URI_SCHEME.match(target)
    ):
        return None

    path_text = target.split("#", 1)[0].split("?", 1)[0]
    if not path_text:
        return None

    if path_text.startswith("/"):
        candidate = root / path_text.lstrip("/")
    else:
        candidate = markdown_file.parent / path_text
    return candidate.resolve(strict=False)


def iter_destinations(markdown_file: Path) -> list[tuple[int, str]]:
    destinations: list[tuple[int, str]] = []
    in_fence = False
    fence_character = ""

    for line_number, line in enumerate(
        markdown_file.read_text(encoding="utf-8").splitlines(), 1
    ):
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_character = marker[0]
            elif marker[0] == fence_character:
                in_fence = False
                fence_character = ""
            continue
        if in_fence:
            continue

        for match in INLINE_LINK.finditer(line):
            destinations.append((line_number, destination(match.group(1))))
        reference = REFERENCE_LINK.match(line)
        if reference:
            destinations.append((line_number, destination(reference.group(1))))
        for match in HTML_LINK.finditer(line):
            destinations.append((line_number, destination(match.group(1))))
    return destinations


def main() -> int:
    root = parse_args().root.resolve()
    if not root.is_dir():
        print(f"error: Markdown-link root is not a directory: {root}", file=sys.stderr)
        return 2

    failures: list[str] = []
    markdown_files = sorted(root.rglob("*.md"))
    for markdown_file in markdown_files:
        for line_number, target in iter_destinations(markdown_file):
            candidate = local_target(root, markdown_file, target)
            if candidate is None:
                continue
            try:
                candidate.relative_to(root)
            except ValueError:
                failures.append(
                    f"{markdown_file.relative_to(root)}:{line_number}: "
                    f"target escapes archive root: {target}"
                )
                continue
            if not candidate.exists():
                failures.append(
                    f"{markdown_file.relative_to(root)}:{line_number}: "
                    f"missing local target: {target}"
                )

    if failures:
        for failure in failures:
            print(f"error: {failure}", file=sys.stderr)
        return 1

    print(
        f"Validated local links in {len(markdown_files)} Markdown files under {root.name}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
