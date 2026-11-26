"""Tests for lint_trivyignore.py.

Run with: python -m pytest .github/scripts/test_lint_trivyignore.py -v
"""
import datetime
import textwrap

import pytest
from ruamel.yaml import YAML

from lint_trivyignore import MAX_EXPIRY_DAYS, lint, main

TODAY = datetime.date(2026, 11, 26)


def _load(text):
    return YAML(typ="safe").load(textwrap.dedent(text))


def _entry(**overrides):
    entry = {
        "id": "CVE-2026-1234",
        "statement": "Not reachable: the vulnerable code path is never invoked.",
        "expired_at": datetime.date(2026, 12, 26),
        "purls": ["pkg:pypi/example@1.2.3"],
    }
    entry.update(overrides)
    return {k: v for k, v in entry.items() if v is not _MISSING}


_MISSING = object()


def _doc(entry):
    return {"vulnerabilities": [entry]}


def test_max_expiry_days():
    assert MAX_EXPIRY_DAYS == 45


VALID_YAML = """\
    vulnerabilities:
      - id: CVE-2026-1234
        statement: Not reachable, the vulnerable code path is never invoked.
        expired_at: 2026-12-26
        purls:
          - pkg:pypi/example@1.2.3
"""


def test_valid_entry_is_clean():
    assert lint(_load(VALID_YAML), TODAY) == []


@pytest.mark.parametrize(
    "document, expected",
    [
        pytest.param(
            {
                "vulnerabilities": [_entry()],
                "misconfigurations": [{"id": "AVD-KSV-0001"}],
            },
            "misconfigurations",
            id="second-top-level-key",
        ),
        pytest.param(_doc(_entry(id=_MISSING)), "id", id="missing-id"),
        pytest.param(_doc(_entry(statement="")), "statement", id="empty-statement"),
        pytest.param(
            _doc(_entry(statement=_MISSING)), "statement", id="missing-statement"
        ),
        pytest.param(
            _doc(_entry(expired_at=_MISSING)), "expired_at", id="missing-expired-at"
        ),
        pytest.param(
            _doc(_entry(expired_at=datetime.date(2027, 1, 11))),
            "45",
            id="46-days-out",
        ),
        pytest.param(
            _doc(_entry(purls=_MISSING)), "purls", id="no-purls-or-paths"
        ),
        pytest.param(
            _doc(_entry(purls=["pkg:pypi/example"])), "@", id="purl-without-version"
        ),
    ],
)
def test_each_rule_fails_alone(document, expected):
    problems = lint(document, TODAY)
    assert len(problems) == 1, problems
    assert expected in problems[0]


@pytest.mark.parametrize(
    "purl, clean",
    [
        pytest.param("pkg:npm/@scope/name", False, id="unencoded-scope-no-version"),
        pytest.param("pkg:npm/%40scope/name@1.2.3", True, id="encoded-scope-versioned"),
        pytest.param("pkg:npm/@scope/name@1.2.3", True, id="unencoded-scope-versioned"),
        pytest.param("pkg:deb/debian/openssl@", False, id="empty-version"),
        pytest.param(
            "pkg:deb/debian/openssl@3.0.15-1~deb12u1?arch=amd64",
            True,
            id="version-with-qualifiers",
        ),
        pytest.param("pkg:deb/debian/openssl?arch=a@b", False, id="at-only-in-qualifier"),
    ],
)
def test_purl_version_detection(purl, clean):
    problems = lint(_doc(_entry(purls=[purl])), TODAY)
    if clean:
        assert problems == []
    else:
        assert len(problems) == 1, problems
        assert "@version" in problems[0]


def test_problem_is_prefixed_with_entry_id_or_index():
    [with_id] = lint(_doc(_entry(statement="")), TODAY)
    assert with_id.startswith("CVE-2026-1234: ")
    [no_id] = lint(_doc(_entry(id=_MISSING)), TODAY)
    assert no_id.startswith("0: ")


def test_paths_alone_satisfy_scope_rule():
    document = _doc(_entry(purls=_MISSING, paths=["usr/lib/foo"]))
    assert lint(document, TODAY) == []


def test_exactly_max_expiry_is_clean():
    expiry = TODAY + datetime.timedelta(days=MAX_EXPIRY_DAYS)
    assert lint(_doc(_entry(expired_at=expiry)), TODAY) == []


def test_past_expiry_is_accepted():
    # Trivy drops expired entries itself, so the scan then fails on the CVE.
    assert lint(_doc(_entry(expired_at=datetime.date(2026, 11, 1))), TODAY) == []


@pytest.mark.parametrize(
    "value, clean",
    [
        pytest.param("2026-12-01", True, id="yaml-date"),
        pytest.param('"2026-12-01"', False, id="quoted-string"),
        pytest.param("2026-12-01T00:00:00Z", True, id="timestamp"),
        pytest.param('"next month"', False, id="garbage"),
    ],
)
def test_expired_at_forms(value, clean):
    text = VALID_YAML.replace("2026-12-26", value)
    problems = lint(_load(text), TODAY)
    if clean:
        assert problems == []
    else:
        assert len(problems) == 1
        assert "expired_at" in problems[0]


@pytest.mark.parametrize(
    "value, message",
    [
        pytest.param(
            "2026-12-01",
            "expired_at must be an unquoted YAML date (Trivy cannot parse a quoted string)",
            id="str",
        ),
        pytest.param(
            20261201, "expired_at must be a YAML date (YYYY-MM-DD)", id="int"
        ),
        pytest.param(
            ["2026-12-01"], "expired_at must be a YAML date (YYYY-MM-DD)", id="list"
        ),
    ],
)
def test_expired_at_message_by_type(value, message):
    assert lint(_doc(_entry(expired_at=value)), TODAY) == [
        f"CVE-2026-1234: {message}"
    ]


@pytest.mark.parametrize(
    "paths, clean",
    [
        pytest.param("x", False, id="string-not-list"),
        pytest.param([], False, id="empty-list"),
        pytest.param([""], False, id="blank-entry"),
        pytest.param(["**"], False, id="double-star"),
        pytest.param(["*"], False, id="star"),
        pytest.param(["**/*"], False, id="double-star-slash-star"),
        pytest.param(["*/**"], False, id="star-slash-double-star"),
        pytest.param(["usr/local/bin/app"], True, id="specific-path"),
    ],
)
def test_paths_must_be_scoped_list(paths, clean):
    problems = lint(_doc(_entry(purls=_MISSING, paths=paths)), TODAY)
    if clean:
        assert problems == []
    else:
        assert len(problems) == 1, problems
        assert "path" in problems[0]


@pytest.mark.parametrize("purls", ["pkg:pypi/a@1", [], [""]])
def test_purls_must_be_list_of_strings(purls):
    problems = lint(_doc(_entry(purls=purls)), TODAY)
    assert len(problems) == 1, problems
    assert "purls" in problems[0]


@pytest.mark.parametrize(
    "contents, argv_extra, expected_code",
    [
        pytest.param(VALID_YAML, [], 0, id="clean"),
        pytest.param(VALID_YAML.replace("2026-12-26", "2027-06-01"), [], 1, id="problems"),
        pytest.param(None, [], 2, id="missing-file"),
        pytest.param("a: [unclosed\n", [], 2, id="non-yaml"),
        pytest.param(VALID_YAML, ["--today", "bogus"], 2, id="bad-today"),
    ],
)
def test_main_exit_codes(tmp_path, capsys, contents, argv_extra, expected_code):
    path = tmp_path / ".trivyignore.yaml"
    if contents is not None:
        path.write_text(textwrap.dedent(contents))
    argv = ["lint_trivyignore.py", str(path), "--today", "2026-11-26"]
    if argv_extra:
        argv = ["lint_trivyignore.py", str(path), *argv_extra]
    assert main(argv) == expected_code
    out = capsys.readouterr()
    if expected_code == 1:
        assert out.out.startswith("CVE-2026-1234: ")
