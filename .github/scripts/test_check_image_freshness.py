"""Tests for check_image_freshness.py.

Run with: python -m pytest .github/scripts/test_check_image_freshness.py -v
"""
import pathlib

import pytest

from check_image_freshness import (
    ParsedTag,
    digest_moved,
    main,
    newer_release,
    parse_tag,
    pinned_digest,
    pinned_tag,
)

TAGS_DIR = pathlib.Path(__file__).parent / "testdata" / "tags"

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _tags(name):
    return (TAGS_DIR / f"{name}.txt").read_text().split()


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("25.12.3-alpine", ParsedTag("25.12.3-alpine", (25, 12, 3), None, "alpine")),
        ("v1.0.0-beta.234", ParsedTag("v1.0.0-beta.234", (1, 0, 0), ("beta", 234), "")),
        ("18.4-system-trixie", ParsedTag("18.4-system-trixie", (18, 4), None, "system-trixie")),
        ("7.4.7", ParsedTag("7.4.7", (7, 4, 7), None, "")),
        ("v0.7.0-rc1", ParsedTag("v0.7.0-rc1", (0, 7, 0), None, "rc1")),
        (
            "v1.0.0-beta.234-nebari.1",
            ParsedTag("v1.0.0-beta.234-nebari.1", (1, 0, 0), ("beta", 234), "nebari.1"),
        ),
        # a dotted suffix that is not an alpha/beta/rc train is a variant
        ("1.2.3-foo.4", ParsedTag("1.2.3-foo.4", (1, 2, 3), None, "foo.4")),
        ("latest", None),
        ("main", None),
        ("sha-abc1234", None),
        ("nightly-20261126", None),
        ("2956a2b342967bf85fe7e5a889c1c64b1b1d56fc", None),
        ("sha256-f25fb21904bfe21f0aa712d35855b216dc9d845be3785ac83b689b08e21733ea.sig", None),
        ("", None),
    ],
)
def test_parse_tag(tag, expected):
    assert parse_tag(tag) == expected


@pytest.mark.parametrize(
    "ref, expected_raw",
    [
        ("redis:7.4.7", "7.4.7"),
        ("docker.io/library/redis:7.4.7@" + DIGEST_A, "7.4.7"),
        ("ghcr.io/llm-d/llm-d-cuda:v0.7.0", "v0.7.0"),
        ("localhost:5000/x:1.2", "1.2"),
        ("localhost:5000/team/x:1.2@" + DIGEST_A, "1.2"),
    ],
)
def test_pinned_tag(ref, expected_raw):
    assert pinned_tag(ref).raw == expected_raw


@pytest.mark.parametrize(
    "ref",
    [
        "redis:latest",
        "redis",
        "redis@" + DIGEST_A,
        "localhost:5000/x",
        "localhost:5000/x@" + DIGEST_A,
        "ghcr.io/x/y:main",
    ],
)
def test_pinned_tag_without_version_raises(ref):
    with pytest.raises(ValueError):
        pinned_tag(ref)


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("redis:7.4.7@" + DIGEST_A, DIGEST_A),
        ("docker.io/library/redis:7.4.7@" + DIGEST_B, DIGEST_B),
        ("localhost:5000/x:1.2@" + DIGEST_A, DIGEST_A),
        ("redis:7.4.7", None),
        ("localhost:5000/x:1.2", None),
    ],
)
def test_pinned_digest(ref, expected):
    assert pinned_digest(ref) == expected


@pytest.mark.parametrize(
    "pin, tags, expected",
    [
        pytest.param(
            "v0.9.0", ["v0.9.0", "v1.0.0", "v0.10.0", "v1.0.0-rc.2"], None,
            id="epp-0.9-not-1.0",
        ),
        pytest.param(
            "v0.9.0", _tags("ghcr-io-llm-d-llm-d-router-endpoint-picker"), None,
            id="epp-recorded",
        ),
        pytest.param(
            "25.12.3-alpine", ["25.12.3-alpine", "26.11.2-alpine"], None,
            id="clickhouse-26-is-off-line",
        ),
        pytest.param(
            "25.12.3-alpine", _tags("clickhouse-clickhouse-server"), "25.12.11.4-alpine",
            id="clickhouse-recorded-4-part",
        ),
        pytest.param(
            "8.0.3", _tags("confluentinc-cp-kafka"), "8.4.1",
            id="cp-kafka-recorded",
        ),
        pytest.param(
            "7.4.7", _tags("redis"), "7.4.12",
            id="redis-recorded",
        ),
    ],
)
def test_same_major(pin, tags, expected):
    assert newer_release(parse_tag(pin), tags) == expected


@pytest.mark.parametrize(
    "pin, extra, expected",
    [
        pytest.param("v0.7.0", [], None, id="recorded"),
        pytest.param("v0.7.0", ["v0.7.1"], "v0.7.1", id="patch-counts"),
        pytest.param("v0.7.0", ["v0.9.0", "v0.8.1"], None, id="next-minor-never-counts"),
    ],
)
def test_zero_x_same_minor(pin, extra, expected):
    tags = _tags("ghcr-io-llm-d-llm-d-cuda") + extra
    assert newer_release(parse_tag(pin), tags) == expected


def test_zero_x_recorded_list_has_off_line_releases():
    # guard: the recorded list does carry v0.9.0, so the None above is the rule
    assert "v0.9.0" in _tags("ghcr-io-llm-d-llm-d-cuda")


@pytest.mark.parametrize(
    "pin, tags, expected",
    [
        pytest.param(
            "18.4-system-trixie",
            ["18.4-system-trixie", "18.9-minimal-trixie", "18.9", "18.8-system-bookworm",
             "18.7-system-trixie"],
            "18.7-system-trixie",
            id="postgres-same-variant-only",
        ),
        pytest.param(
            "18.4-system-trixie", _tags("ghcr-io-cloudnative-pg-postgresql"),
            "18.7-system-trixie",
            id="postgres-recorded",
        ),
        pytest.param(
            "25.12.3-alpine", ["25.12.3-alpine", "25.12.9", "25.12.8-alpine3.21"], None,
            id="clickhouse-ignores-non-alpine",
        ),
        pytest.param(
            "7.4.7", ["7.4.7", "7.4.9-alpine", "7.4.9-bookworm"], None,
            id="plain-pin-ignores-variants",
        ),
        pytest.param(
            "18.4-system-trixie", ["18.4.0-system-trixie"], None,
            id="trailing-zero-is-not-newer",
        ),
        pytest.param(
            "18.4.0-system-trixie", ["18.4-system-trixie", "18.4.1-system-trixie"],
            "18.4.1-system-trixie",
            id="trailing-zero-pin-still-sees-patch",
        ),
    ],
)
def test_variant(pin, tags, expected):
    assert newer_release(parse_tag(pin), tags) == expected


@pytest.mark.parametrize(
    "pin, tags, expected",
    [
        pytest.param(
            "v1.0.0-beta.234", ["v1.0.0-beta.234", "v1.0.0-beta.239", "v1.0.0-beta.235"],
            "v1.0.0-beta.239",
            id="same-train",
        ),
        pytest.param(
            "v1.0.0-beta.234", _tags("ghcr-io-openmeterio-benthos-collector"),
            "v1.0.0-beta.239",
            id="benthos-recorded",
        ),
        pytest.param(
            "v1.0.0-beta.234", ["v1.0.0-beta.239", "v1.0.0"], "v1.0.0",
            id="stable-of-same-release-beats-beta",
        ),
        pytest.param(
            "v1.0.0-beta.234", ["v1.0.0-rc.1", "v1.0.1-beta.1", "v1.0.0-alpha.900"], None,
            id="other-train-or-release-never-counts",
        ),
        pytest.param(
            "v1.0.0-beta.234", ["v1.0-beta.239"], "v1.0-beta.239",
            id="trailing-zero-same-release-for-train",
        ),
        pytest.param(
            "v1.0.0", ["v1.0.1-beta.5", "v1.1.0-rc.1", "v1.0.0-beta.999"], None,
            id="stable-pin-ignores-betas",
        ),
        pytest.param(
            "v1.0.0", ["v1.0.1-beta.5", "v1.0.1"], "v1.0.1",
            id="stable-pin-takes-stable",
        ),
    ],
)
def test_beta_train(pin, tags, expected):
    assert newer_release(parse_tag(pin), tags) == expected


@pytest.mark.parametrize(
    "pinned, current, expected",
    [
        (DIGEST_A, DIGEST_B, True),
        (DIGEST_A, DIGEST_A, False),
        (None, DIGEST_B, False),
        (DIGEST_A, None, False),
        (None, None, False),
    ],
)
def test_digest_moved(pinned, current, expected):
    assert digest_moved(pinned, current) is expected


@pytest.mark.parametrize(
    "pin, extra, expected",
    [
        pytest.param("v0.7.0", ["latest", "sha-abc1234", "nightly-20261126", "main",
                                 "v0.7.0-rc1"], None, id="noise-never-returned"),
        pytest.param("v0.7.0", ["0.7.3"], "0.7.3", id="v-pin-unprefixed-candidate"),
        pytest.param("0.7.0", ["v0.7.2"], "v0.7.2", id="unprefixed-pin-v-candidate"),
        pytest.param("v0.7.0", ["0.7.0"], None, id="same-numbers-is-not-newer"),
    ],
)
def test_tag_grammar_noise(pin, extra, expected):
    tags = ["v0.7.0"] + extra
    assert newer_release(parse_tag(pin), tags) == expected


@pytest.fixture
def tags_file(tmp_path):
    def write(tags):
        path = tmp_path / "tags.txt"
        path.write_text("\n".join(tags) + "\n")
        return str(path)
    return write


@pytest.mark.parametrize(
    "ref, tags, digest_args, expected_code, expected_text",
    [
        pytest.param("redis:7.4.7", ["7.4.7", "7.4.6"], [], 0, "", id="nothing-newer"),
        pytest.param("redis:7.4.7", ["7.4.7", "7.4.12"], [], 1, "7.4.12", id="newer-tag"),
        pytest.param(
            "docker.io/library/redis:7.4.7@" + DIGEST_A, ["7.4.7"],
            ["--tag-digest", DIGEST_B], 1, f"digest {DIGEST_A} -> {DIGEST_B}",
            id="digest-moved",
        ),
        pytest.param(
            "redis:7.4.7@" + DIGEST_A, ["7.4.7"], ["--tag-digest", DIGEST_A], 0, "",
            id="digest-same",
        ),
        pytest.param(
            "redis:7.4.7", ["7.4.7"], ["--tag-digest", DIGEST_B], 0, "",
            id="no-pinned-digest",
        ),
        pytest.param(
            "redis:7.4.7@" + DIGEST_A, ["7.4.7", "7.4.12"], ["--tag-digest", DIGEST_B],
            1, "7.4.12", id="newer-tag-wins-over-digest",
        ),
        pytest.param("redis:latest", ["latest", "7.4.12"], [], 2, "", id="no-version-tag"),
    ],
)
def test_main_exit_codes(tags_file, capsys, ref, tags, digest_args,
                         expected_code, expected_text):
    argv = ["check_image_freshness.py", ref, "--tags-file", tags_file(tags), *digest_args]
    assert main(argv) == expected_code
    out = capsys.readouterr().out
    if expected_code == 1:
        assert len(out.splitlines()) == 1
        assert expected_text in out
        if expected_text == "7.4.12":
            assert "digest" not in out
    else:
        assert out == ""


def test_main_unreadable_tags_file(tmp_path, capsys):
    missing = str(tmp_path / "missing.txt")
    assert main(["check_image_freshness.py", "redis:7.4.7", "--tags-file", missing]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_main_usage_error():
    assert main(["check_image_freshness.py"]) == 2
