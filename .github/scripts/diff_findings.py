#!/usr/bin/env python3
"""Report the Trivy findings in a scan that are not in a baseline scan.

Run by the build-time scan on an ordinary pull request: the job fails only
on findings that are new against the baseline published from `main`'s
scheduled scan, so a PR is not blocked by findings it did not introduce.

Usage:
    python diff_findings.py --current FILE [--baseline FILE]

Both files are `trivy --format json` reports (the shape `findings-file`
writes). The scan already filters severity and unfixed findings, so every
vulnerability in the report counts.

Rules:
  - a finding is keyed by (VulnerabilityID, PkgName); the installed
    version is not part of the key, so a package upgrade that keeps a CVE
    is still the same finding, and a CVE in a differently named package is
    new
  - no --baseline, or a --baseline that is missing, unreadable or not a
    valid report, means no baseline: every current finding is new (fail
    closed); the unreadable cases print one line on stderr saying so
  - a baseline report with no Results is a baseline with no findings

Exit codes: 0 nothing new, 1 something new (one "<vuln_id> <package>" line
per new finding on stdout, sorted), 2 usage error or a --current file that
is missing or not a valid Trivy JSON report (the caller treats any code
other than 0 or 1 as an error).
"""
import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True, order=True)
class Finding:
    vuln_id: str   # "CVE-2026-12345"
    package: str   # package name as Trivy reports it; the installed version is not part of the key


def read_findings(trivy_json: Any) -> set[Finding]:
    """The findings in a trivy --format json report.

    Raises ValueError when the report does not have the Trivy shape.
    """
    if not isinstance(trivy_json, dict):
        raise ValueError("report is not a JSON object")
    results = trivy_json.get("Results")
    if results is None:
        results = []
    elif not isinstance(results, list):
        raise ValueError("Results is not a list or null")
    findings = set()
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("a Results entry is not an object")
        vulns = result.get("Vulnerabilities")
        if vulns is None:
            vulns = []
        elif not isinstance(vulns, list):
            raise ValueError("Vulnerabilities is not a list or null")
        for vuln in vulns:
            try:
                vuln_id, package = vuln["VulnerabilityID"], vuln["PkgName"]
            except (KeyError, TypeError) as exc:
                raise ValueError(f"a vulnerability has no VulnerabilityID or PkgName: {vuln!r}") from exc
            if not isinstance(vuln_id, str) or not isinstance(package, str):
                raise ValueError(f"a vulnerability has a non-string VulnerabilityID or PkgName: {vuln!r}")
            findings.add(Finding(vuln_id, package))
    return findings


def new_findings(current: set[Finding], baseline: set[Finding] | None) -> set[Finding]:
    """The current findings not in the baseline; all of them when there is no baseline."""
    if baseline is None:
        return set(current)
    return current - baseline


def _load(path: str) -> set[Finding]:
    """Read a report file; raises OSError or ValueError (json errors included)."""
    with open(path, encoding="utf-8") as f:
        return read_findings(json.load(f))


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="diff_findings.py")
    parser.add_argument("--current", required=True)
    parser.add_argument("--baseline", default=None)
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return 2 if exc.code else 0

    try:
        current = _load(args.current)
    except Exception as exc:  # noqa: BLE001 - any failure (RecursionError too) must be 2, never 1
        print(f"diff_findings: error: --current {args.current}: {exc}", file=sys.stderr)
        return 2

    baseline = None
    if args.baseline is not None:
        try:
            baseline = _load(args.baseline)
        except Exception as exc:  # noqa: BLE001 - an uncaught exception would exit 1
            print(
                f"diff_findings: warning: could not read baseline {args.baseline} "
                f"({exc}); treating every finding as new",
                file=sys.stderr,
            )

    new = new_findings(current, baseline)
    for finding in sorted(new):
        print(f"{finding.vuln_id} {finding.package}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
