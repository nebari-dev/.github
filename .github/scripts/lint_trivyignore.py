#!/usr/bin/env python3
"""Lint a Trivy .trivyignore.yaml file.

Run by the CVE-scanning workflows before the scan, so that every ignore
entry is justified, scoped and short-lived.

Usage:
    python lint_trivyignore.py FILE [--today YYYY-MM-DD]

Rules:
  - the only top-level key is "vulnerabilities"
  - every entry has a non-empty "id" and "statement"
  - every entry has an "expired_at" no more than MAX_EXPIRY_DAYS days from
    today. A date in the past is accepted on purpose: Trivy silently drops
    expired entries, so the scan then fails on the CVE.
  - every entry is scoped by "purls" (each with an @version) or "paths"

Exit codes: 0 clean, 1 problems (one line each on stdout, formatted
"<entry id or index>: <problem>"), 2 usage error or unreadable YAML.
"""
import argparse
import datetime
import sys
from typing import Any, Sequence

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

MAX_EXPIRY_DAYS = 45


def _parse_expiry(value: Any) -> datetime.date | None:
    # ruamel's safe loader yields datetime.date / datetime.datetime for
    # unquoted timestamps; quoted ones arrive as strings.
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return datetime.date.fromisoformat(text)
        except ValueError:
            pass
        try:
            return datetime.datetime.fromisoformat(text).date()
        except ValueError:
            return None
    return None


def _has_version(purl: Any) -> bool:
    # The version lives in the last path segment, before any ?qualifiers or
    # #subpath. An "@" earlier (an unencoded npm scope) is not a version.
    if not isinstance(purl, str):
        return False
    last = purl.split("#", 1)[0].split("?", 1)[0].rpartition("/")[2]
    _, at, version = last.rpartition("@")
    return bool(at) and bool(version)


def _blank(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


def _lint_entry(entry: Any, today: datetime.date) -> list[str]:
    if not isinstance(entry, dict):
        return ["entry must be a mapping"]
    problems = []
    if _blank(entry.get("id")):
        problems.append("missing id")
    if _blank(entry.get("statement")):
        problems.append("missing or empty statement")

    if "expired_at" not in entry or entry["expired_at"] is None:
        problems.append("missing expired_at")
    else:
        expiry = _parse_expiry(entry["expired_at"])
        if expiry is None:
            problems.append(
                f"expired_at {entry['expired_at']!r} is not a valid date"
            )
        elif (expiry - today).days > MAX_EXPIRY_DAYS:
            problems.append(
                f"expired_at {expiry.isoformat()} is more than "
                f"{MAX_EXPIRY_DAYS} days from {today.isoformat()}"
            )

    purls = entry.get("purls")
    paths = entry.get("paths")
    if not purls and not paths:
        problems.append("needs purls or paths to scope the ignore")
    if purls:
        if not isinstance(purls, list):
            problems.append("purls must be a list")
        else:
            for purl in purls:
                if not _has_version(purl):
                    problems.append(f"purl {purl!r} has no @version")
    return problems


def lint(document: Any, today: datetime.date) -> list[str]:
    """Return one message per problem found in a parsed .trivyignore.yaml."""
    if not isinstance(document, dict):
        return ["document: top level must be a mapping"]
    problems = []
    for key in document:
        if key != "vulnerabilities":
            problems.append(
                f"{key}: only the 'vulnerabilities' top-level key is allowed"
            )
    entries = document.get("vulnerabilities") or []
    if not isinstance(entries, list):
        return problems + ["vulnerabilities: must be a list"]
    for index, entry in enumerate(entries):
        label = index
        if isinstance(entry, dict) and not _blank(entry.get("id")):
            label = entry["id"]
        problems.extend(f"{label}: {p}" for p in _lint_entry(entry, today))
    return problems


def _iso_date(text: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="lint_trivyignore.py")
    parser.add_argument("file")
    parser.add_argument("--today", type=_iso_date, default=None)
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return 2 if exc.code else 0
    today = args.today or datetime.datetime.now(datetime.timezone.utc).date()

    try:
        with open(args.file) as f:
            document = YAML(typ="safe").load(f)
    except (OSError, YAMLError) as exc:
        print(f"lint_trivyignore: error: {exc}", file=sys.stderr)
        return 2

    problems = lint(document, today)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
