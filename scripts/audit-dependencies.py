#!/usr/bin/env python3
"""Query OSV for vulnerabilities in the Maven artifacts pinned by dependencies.lock."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_API_URL = "https://api.osv.dev/v1/query"


def parse_args() -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lock",
        type=Path,
        default=repository_root / "dependencies.lock",
        help="dependency lock file to audit",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def maven_purl(url: str) -> str:
    path = urllib.parse.urlsplit(url).path
    marker = "/maven2/"
    if marker not in path:
        raise ValueError(f"dependency URL is not a Maven Central artifact URL: {url}")

    parts = path.split(marker, 1)[1].split("/")
    if len(parts) < 4:
        raise ValueError(f"dependency URL does not contain Maven coordinates: {url}")

    group = ".".join(parts[:-3])
    artifact = parts[-3]
    version = parts[-2]
    filename = parts[-1]
    expected_prefix = f"{artifact}-{version}"
    if not filename.startswith(expected_prefix) or not filename.endswith(".jar"):
        raise ValueError(f"dependency filename does not match Maven coordinates: {url}")

    quote = urllib.parse.quote
    return (
        "pkg:maven/"
        f"{quote(group, safe='.-_')}/{quote(artifact, safe='.-_')}"
        f"@{quote(version, safe='.-_+')}"
    )


def read_dependencies(lock_file: Path) -> list[tuple[str, str]]:
    dependencies: list[tuple[str, str]] = []
    with lock_file.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) != 3:
                raise ValueError(
                    f"{lock_file}:{line_number}: expected filename, SHA-256, and URL"
                )
            filename, checksum, url = fields
            if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
                raise ValueError(
                    f"{lock_file}:{line_number}: invalid lowercase SHA-256"
                )
            dependencies.append((filename, maven_purl(url)))

    if not dependencies:
        raise ValueError(f"{lock_file}: no dependencies found")
    return dependencies


def query_osv(api_url: str, purl: str) -> list[dict[str, object]]:
    request = urllib.request.Request(
        api_url,
        data=json.dumps({"package": {"purl": purl}}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "dUMI-dependency-audit/1",
        },
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            vulnerabilities = result.get("vulns", [])
            if not isinstance(vulnerabilities, list):
                raise RuntimeError("OSV returned an invalid vulnerabilities field")
            return [
                vulnerability
                for vulnerability in vulnerabilities
                if isinstance(vulnerability, dict) and not vulnerability.get("withdrawn")
            ]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2**attempt)

    raise RuntimeError(f"OSV query failed for {purl}: {last_error}")


def main() -> int:
    args = parse_args()
    try:
        dependencies = read_dependencies(args.lock)
        findings: list[tuple[str, str, dict[str, object]]] = []
        for filename, purl in dependencies:
            print(f"Auditing {filename} ({purl})")
            for vulnerability in query_osv(args.api_url, purl):
                findings.append((filename, purl, vulnerability))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not findings:
        print(f"OSV found no active advisories for {len(dependencies)} locked dependencies.")
        return 0

    for filename, purl, vulnerability in findings:
        vulnerability_id = str(vulnerability.get("id", "unknown"))
        summary = str(vulnerability.get("summary", "No summary supplied")).replace(
            "\n", " "
        )
        aliases = vulnerability.get("aliases", [])
        alias_text = ", ".join(str(alias) for alias in aliases) if aliases else "none"
        message = (
            f"{filename}: {vulnerability_id}: {summary} "
            f"(aliases: {alias_text}; package: {purl})"
        )
        print(f"error: {message}", file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(
                f"::error title=Vulnerable locked dependency::{message}",
                file=sys.stderr,
            )
    return 1


if __name__ == "__main__":
    sys.exit(main())
