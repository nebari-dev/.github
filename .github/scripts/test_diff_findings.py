"""Tests for diff_findings.py.

Run with: python -m pytest .github/scripts/test_diff_findings.py -v
"""
import json

import pytest

from diff_findings import Finding, main, new_findings, read_findings


def _vuln(vuln_id, package, installed="1.0.0", fixed="1.0.1", severity="HIGH"):
    return {
        "VulnerabilityID": vuln_id,
        "PkgName": package,
        "InstalledVersion": installed,
        "FixedVersion": fixed,
        "Severity": severity,
    }


def _report(*vulns):
    """A trivy --format json report with every vuln in one result."""
    return {
        "SchemaVersion": 2,
        "ArtifactName": "ghcr.io/example/image:1.0.0",
        "Results": [
            {"Target": "debian 12", "Class": "os-pkgs", "Vulnerabilities": list(vulns)},
        ],
    }


OPENSSL_CVE = _vuln("CVE-2026-0001", "openssl", installed="3.0.15-1")
BASELINE = _report(OPENSSL_CVE)


@pytest.mark.parametrize(
    "report, expected",
    [
        (BASELINE, {Finding("CVE-2026-0001", "openssl")}),
        ({}, set()),
        ({"Results": None}, set()),
        ({"Results": [{"Target": "x"}]}, set()),
        ({"Results": [{"Target": "x", "Vulnerabilities": None}]}, set()),
        (
            {
                "Results": [
                    {"Target": "debian 12", "Vulnerabilities": [_vuln("CVE-2026-0001", "openssl")]},
                    {"Target": "app/go.mod", "Vulnerabilities": [
                        _vuln("CVE-2026-0002", "golang.org/x/net"),
                        _vuln("CVE-2026-0001", "openssl", installed="3.0.16-1"),
                    ]},
                ]
            },
            {Finding("CVE-2026-0001", "openssl"), Finding("CVE-2026-0002", "golang.org/x/net")},
        ),
    ],
    ids=["one", "no-results-key", "null-results", "no-vulns-key", "null-vulns", "many-results-dedup"],
)
def test_read_findings(report, expected):
    assert read_findings(report) == expected


@pytest.mark.parametrize(
    "current, baseline, expected",
    [
        # a baseline with no Results is an empty set, not a missing baseline
        (_report(OPENSSL_CVE), {"Results": None}, {Finding("CVE-2026-0001", "openssl")}),
        # a finding fixed since the baseline is simply absent: nothing new
        (_report(), BASELINE, set()),
    ],
    ids=["empty-baseline", "fixed-finding-gone"],
)
def test_new_findings(current, baseline, expected):
    assert new_findings(read_findings(current), read_findings(baseline)) == expected


def test_same_finding_not_new():
    assert new_findings(read_findings(BASELINE), read_findings(BASELINE)) == set()


def test_new_cve_known_package_is_new():
    current = read_findings(_report(OPENSSL_CVE, _vuln("CVE-2026-0009", "openssl")))
    assert new_findings(current, read_findings(BASELINE)) == {Finding("CVE-2026-0009", "openssl")}


def test_known_cve_new_package_is_new():
    current = read_findings(_report(OPENSSL_CVE, _vuln("CVE-2026-0001", "libssl3")))
    assert new_findings(current, read_findings(BASELINE)) == {Finding("CVE-2026-0001", "libssl3")}


def test_installed_version_change_not_new():
    current = read_findings(_report(_vuln("CVE-2026-0001", "openssl", installed="3.0.16-1")))
    assert new_findings(current, read_findings(BASELINE)) == set()


def test_no_baseline_everything_new():
    current = read_findings(_report(OPENSSL_CVE))
    assert new_findings(current, None) == current


@pytest.fixture
def write_json(tmp_path):
    def _write(name, content):
        path = tmp_path / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return str(path)
    return _write


CURRENT_TWO_NEW = _report(
    OPENSSL_CVE,
    _vuln("CVE-2026-0003", "zlib"),
    _vuln("CVE-2026-0002", "zlib"),
)


@pytest.mark.parametrize(
    "current, baseline, expected_code, expected_out, expect_baseline_warning",
    [
        (_report(OPENSSL_CVE), BASELINE, 0, "", False),
        (CURRENT_TWO_NEW, BASELINE, 1, "CVE-2026-0002 zlib\nCVE-2026-0003 zlib\n", False),
        # missing --baseline: fail closed, everything is new, no warning
        (_report(OPENSSL_CVE), None, 1, "CVE-2026-0001 openssl\n", False),
        # --baseline names a file that does not exist
        (_report(OPENSSL_CVE), "<missing>", 1, "CVE-2026-0001 openssl\n", True),
        # --baseline is not valid JSON
        (_report(OPENSSL_CVE), "not json {", 1, "CVE-2026-0001 openssl\n", True),
        # --baseline is valid JSON but not a Trivy report: treated as unreadable
        (_report(OPENSSL_CVE), '["a", "list"]', 1, "CVE-2026-0001 openssl\n", True),
        (_report(OPENSSL_CVE), {"Results": {}}, 1, "CVE-2026-0001 openssl\n", True),
        # --baseline is valid JSON with no Results: nothing in the baseline
        (_report(OPENSSL_CVE), {"SchemaVersion": 2}, 1, "CVE-2026-0001 openssl\n", False),
        # no findings at all with no baseline: nothing new
        (_report(), None, 0, "", False),
    ],
    ids=["nothing-new", "two-new-sorted", "no-baseline-arg", "baseline-missing-file",
         "baseline-invalid-json", "baseline-not-an-object", "baseline-results-not-a-list",
         "baseline-no-results", "clean-no-baseline"],
)
def test_exit_codes(write_json, tmp_path, capsys, current, baseline, expected_code,
                    expected_out, expect_baseline_warning):
    argv = ["diff_findings.py", "--current", write_json("current.json", current)]
    if baseline == "<missing>":
        argv += ["--baseline", str(tmp_path / "does-not-exist.json")]
    elif baseline is not None:
        argv += ["--baseline", write_json("baseline.json", baseline)]
    assert main(argv) == expected_code
    captured = capsys.readouterr()
    assert captured.out == expected_out
    assert ("baseline" in captured.err) == expect_baseline_warning
    if expect_baseline_warning:
        assert len(captured.err.strip().splitlines()) == 1


@pytest.mark.parametrize(
    "current",
    [
        "<missing>",
        "not json {",
        '["a", "list"]',
        '{"Results": "nope"}',
        '{"Results": {}}',
        '{"Results": ""}',
        '{"Results": [{"Vulnerabilities": {}}]}',
        '{"Results": [{"Vulnerabilities": ""}]}',
        '{"Results": [{"Vulnerabilities": [{}]}]}',
        "[" * 100000 + "]" * 100000,
        b"\xff\xfe not utf-8",
    ],
    ids=["missing", "invalid-json", "not-an-object", "results-not-a-list", "results-empty-object",
         "results-empty-string", "vulns-empty-object", "vulns-empty-string", "vuln-missing-keys",
         "deeply-nested", "not-utf8"],
)
def test_bad_current_is_exit_2(write_json, tmp_path, capsys, current):
    path = str(tmp_path / "nope.json") if current == "<missing>" else write_json("current.json", current)
    assert main(["diff_findings.py", "--current", path]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "diff_findings" in captured.err


def test_usage_is_exit_2(capsys):
    assert main(["diff_findings.py"]) == 2
    assert main(["diff_findings.py", "--current", "x", "--bogus"]) == 2
