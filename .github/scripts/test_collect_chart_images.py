"""Tests for collect_chart_images.py.

Run with: python -m pytest .github/scripts/test_collect_chart_images.py -v
"""
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from collect_chart_images import (
    ImageEntry,
    build_entries,
    collect,
    main,
    slugify,
    to_matrix,
)

DEFAULT_ENV = re.compile(r"_IMAGE$")
OWN = "ghcr.io/nebari-dev/llm-serving-pack/"
OPENMETER = "ghcr.io/nebari-dev/openmeter"
DIGEST = "sha256:" + "a" * 64


def _docs(text):
    return list(YAML(typ="safe").load_all(textwrap.dedent(text)))


DEPLOYMENT = """\
    apiVersion: apps/v1
    kind: Deployment
    spec:
      template:
        spec:
          initContainers:
            - name: init
              image: busybox:1.37.0
          containers:
            - name: app
              image: ghcr.io/nebari-dev/llm-serving-pack/operator:latest
"""

CRONJOB = """\
    apiVersion: batch/v1
    kind: CronJob
    spec:
      jobTemplate:
        spec:
          template:
            spec:
              containers:
                - name: job
                  image: bitnami/kubectl:1.31
"""

DEEP_CRD = """\
    apiVersion: example.com/v1
    kind: Whatever
    spec:
      foo:
        bar:
          containers:
            - name: deep
              image: ghcr.io/llm-d/llm-d-cuda:v0.7.0
"""

EPHEMERAL = """\
    apiVersion: v1
    kind: Pod
    spec:
      ephemeralContainers:
        - name: debug
          image: nicolaka/netshoot:v0.13
"""


@pytest.mark.parametrize(
    "text, expected",
    [
        pytest.param(
            DEPLOYMENT,
            ["busybox:1.37.0", "ghcr.io/nebari-dev/llm-serving-pack/operator:latest"],
            id="deployment-with-init",
        ),
        pytest.param(CRONJOB, ["bitnami/kubectl:1.31"], id="cronjob"),
        pytest.param(DEEP_CRD, ["ghcr.io/llm-d/llm-d-cuda:v0.7.0"], id="crd-nested"),
        pytest.param(EPHEMERAL, ["nicolaka/netshoot:v0.13"], id="ephemeral"),
        pytest.param(
            DEPLOYMENT + "---\n" + CRONJOB + "---\n" + DEEP_CRD,
            [
                "busybox:1.37.0",
                "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
                "bitnami/kubectl:1.31",
                "ghcr.io/llm-d/llm-d-cuda:v0.7.0",
            ],
            id="multi-document",
        ),
    ],
)
def test_pod_spec_walk_any_depth(text, expected):
    assert collect(_docs(text), DEFAULT_ENV, []) == expected


ENV_DOC = """\
    kind: Deployment
    spec:
      template:
        spec:
          containers:
            - name: operator
              image: op:1
              env:
                - name: LLM_MODEL_DOWNLOADER_IMAGE
                  value: ghcr.io/nebari-dev/llm-serving-pack/model-downloader:latest
                - name: IMAGE_PULL_POLICY
                  value: IfNotPresent
                - name: OTHER
                  value: not-an-image
"""


@pytest.mark.parametrize(
    "env_regex, expected",
    [
        pytest.param(
            DEFAULT_ENV,
            ["op:1", "ghcr.io/nebari-dev/llm-serving-pack/model-downloader:latest"],
            id="default-regex",
        ),
        pytest.param(
            re.compile(r"^NOMATCH$"), ["op:1"], id="regex-matching-nothing"
        ),
    ],
)
def test_env_regex(env_regex, expected):
    assert collect(_docs(ENV_DOC), env_regex, []) == expected


def test_image_name_field():
    text = """\
        apiVersion: postgresql.cnpg.io/v1
        kind: Cluster
        spec:
          imageName: ghcr.io/cloudnative-pg/postgresql:17.5@sha256:%s
    """ % ("b" * 64)
    assert collect(_docs(text), DEFAULT_ENV, []) == [
        "ghcr.io/cloudnative-pg/postgresql:17.5@sha256:" + "b" * 64
    ]


@pytest.mark.parametrize(
    "text, extra, expected",
    [
        pytest.param(
            DEPLOYMENT,
            ["redis:7.4.7"],
            [
                "busybox:1.37.0",
                "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
                "redis:7.4.7",
            ],
            id="extra-appended",
        ),
        pytest.param(
            DEPLOYMENT,
            ["busybox:1.37.0", "redis:7.4.7", "redis:7.4.7"],
            [
                "busybox:1.37.0",
                "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
                "redis:7.4.7",
            ],
            id="extra-duplicates-rendered-and-itself",
        ),
        pytest.param(
            DEPLOYMENT + "---\n" + DEPLOYMENT,
            [],
            [
                "busybox:1.37.0",
                "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
            ],
            id="rendered-twice",
        ),
    ],
)
def test_extra_images_and_dedupe(text, extra, expected):
    assert collect(_docs(text), DEFAULT_ENV, extra) == expected


@pytest.mark.parametrize(
    "ref, first_party_prefixes, origin",
    [
        pytest.param(
            "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
            [],
            "first-party",
            id="own-prefix",
        ),
        pytest.param(
            "GHCR.io/Nebari-Dev/llm-serving-pack/operator:latest",
            [],
            "first-party",
            id="own-prefix-case-insensitive",
        ),
        pytest.param(
            "ghcr.io/nebari-dev/openmeter:v1.0.0-beta.239-nebari.1@" + DIGEST,
            [OPENMETER],
            "first-party",
            id="first-party-prefix",
        ),
        pytest.param(
            "ghcr.io/nebari-dev/openmeter:v1.0.0-beta.239-nebari.1@" + DIGEST,
            [],
            "third-party",
            id="fork-without-prefix",
        ),
        pytest.param(
            "ghcr.io/nebari-dev/openmeter-evil:v1",
            [OPENMETER],
            "third-party",
            id="prefix-matches-on-path-boundary",
        ),
        pytest.param(
            "redis:7.4.7@" + DIGEST, [OPENMETER], "third-party", id="redis"
        ),
    ],
)
def test_origin(ref, first_party_prefixes, origin):
    [entry] = build_entries([ref], "source", OWN, first_party_prefixes)
    assert entry.origin == origin


@pytest.mark.parametrize(
    "ref, own_floating",
    [
        pytest.param(OWN + "operator:latest", True, id="own-latest"),
        pytest.param(OWN + "operator", True, id="own-no-tag"),
        pytest.param(OWN + "operator:sha-a8e74a3", False, id="own-sha-tag"),
        pytest.param(OWN + "operator:latest@" + DIGEST, False, id="own-latest-digest"),
        pytest.param(OWN + "operator@" + DIGEST, False, id="own-digest-only"),
        pytest.param(OPENMETER + ":latest", False, id="first-party-prefix-latest"),
        pytest.param("redis:latest", False, id="third-party-latest"),
        pytest.param("redis", False, id="third-party-no-tag"),
    ],
)
def test_own_floating(ref, own_floating):
    [entry] = build_entries([ref], "source", OWN, [OPENMETER])
    assert entry.own_floating is own_floating


def test_own_floating_needs_own_prefix():
    [entry] = build_entries([OWN + "operator:latest"], "source", "", [])
    assert entry.origin == "third-party"
    assert entry.own_floating is False


@pytest.mark.parametrize(
    "ref, slug",
    [
        pytest.param(
            "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
            "ghcr-io-nebari-dev-llm-serving-pack-operator",
            id="operator",
        ),
        pytest.param(
            "ghcr.io/nebari-dev/openmeter:v1.0.0-beta.239-nebari.1@" + DIGEST,
            "ghcr-io-nebari-dev-openmeter",
            id="openmeter",
        ),
        pytest.param("redis:7.4.12@" + DIGEST, "redis", id="redis-source"),
        pytest.param("redis:7.4.7@" + DIGEST, "redis", id="redis-released"),
        pytest.param(
            "ghcr.io/llm-d/llm-d-cuda:v0.7.0", "ghcr-io-llm-d-llm-d-cuda", id="llm-d"
        ),
        pytest.param(
            "localhost:5000/team/app:1.0", "localhost-5000-team-app", id="registry-port"
        ),
        pytest.param(
            "localhost:5000/team/app", "localhost-5000-team-app", id="port-no-tag"
        ),
        pytest.param(
            "ghcr.io/nebari-dev/llm-serving-pack/operator",
            "ghcr-io-nebari-dev-llm-serving-pack-operator",
            id="bare-repository",
        ),
    ],
)
def test_slug(ref, slug):
    [entry] = build_entries([ref], "source", OWN, [OPENMETER])
    assert entry.slug == slug


def test_slugify_repository():
    assert slugify("ghcr.io/nebari-dev/llm-serving-pack/operator") == (
        "ghcr-io-nebari-dev-llm-serving-pack-operator"
    )


@pytest.mark.parametrize(
    "ref",
    [
        "ghcr.io/nebari-dev/llm-serving-pack/operator",
        "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
        "ghcr.io/nebari-dev/llm-serving-pack/operator:sha-a8e74a3",
        "ghcr.io/nebari-dev/llm-serving-pack/operator@" + DIGEST,
        "localhost:5000/team/app:1.0@" + DIGEST,
    ],
)
def test_slug_cli_matches_matrix_slug(ref, capsys):
    assert main(["collect_chart_images.py", "--slug", ref]) == 0
    printed = capsys.readouterr().out.strip()
    [entry] = build_entries([ref], "source", OWN, [])
    assert printed == entry.slug


# The build job's baseline fetch runs --slug with the runner's python3, which
# has no ruamel.yaml. A None entry in sys.modules makes any import of ruamel
# raise ImportError, as if it were not installed.
_NO_RUAMEL = (
    "import runpy, sys\n"
    "sys.modules['ruamel'] = None\n"
    "sys.modules['ruamel.yaml'] = None\n"
    "sys.argv = sys.argv[1:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)
_SCRIPT = str(Path(__file__).with_name("collect_chart_images.py"))


@pytest.mark.parametrize(
    "ref, slug",
    [
        ("ghcr.io/nebari-dev/llm-serving-pack/operator", "ghcr-io-nebari-dev-llm-serving-pack-operator"),
        ("ghcr.io/nebari-dev/llm-serving-pack/operator:latest", "ghcr-io-nebari-dev-llm-serving-pack-operator"),
        ("redis:7.4.12@" + DIGEST, "redis"),
    ],
)
def test_slug_cli_needs_no_ruamel(ref, slug):
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _NO_RUAMEL, _SCRIPT, "--slug", ref],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == slug + "\n"


def test_rendered_mode_still_needs_ruamel(tmp_path):
    rendered = tmp_path / "r.yaml"
    rendered.write_text("kind: Pod\n")
    proc = subprocess.run(
        [sys.executable, "-I", "-c", _NO_RUAMEL, _SCRIPT, "--rendered", str(rendered), "--leg", "source"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "ruamel" in proc.stderr


def test_slug_stable_across_tags_and_digests():
    refs = [
        "ghcr.io/nebari-dev/llm-serving-pack/operator",
        "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
        "ghcr.io/nebari-dev/llm-serving-pack/operator:sha-a8e74a3",
        "ghcr.io/nebari-dev/llm-serving-pack/operator:latest@" + DIGEST,
    ]
    assert len({e.slug for e in build_entries(refs, "source", OWN, [])}) == 1


def test_to_matrix_matches_spec_example():
    entries = [
        ImageEntry(
            "source",
            "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
            "ghcr-io-nebari-dev-llm-serving-pack-operator",
            "first-party",
            True,
        ),
        ImageEntry("released", "redis:7.4.7@" + DIGEST, "redis", "third-party", False),
    ]
    assert to_matrix(entries) == {
        "include": [
            {
                "leg": "source",
                "ref": "ghcr.io/nebari-dev/llm-serving-pack/operator:latest",
                "slug": "ghcr-io-nebari-dev-llm-serving-pack-operator",
                "origin": "first-party",
                "own-floating": True,
            },
            {
                "leg": "released",
                "ref": "redis:7.4.7@" + DIGEST,
                "slug": "redis",
                "origin": "third-party",
                "own-floating": False,
            },
        ]
    }


def test_main_prints_matrix(tmp_path, capsys):
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(textwrap.dedent(DEPLOYMENT))
    code = main(
        [
            "collect_chart_images.py",
            "--rendered", str(rendered),
            "--leg", "source",
            "--own-prefix", OWN,
            "--first-party-prefix", OPENMETER,
            "--extra-image", OPENMETER + ":v1@" + DIGEST,
        ]
    )
    assert code == 0
    matrix = json.loads(capsys.readouterr().out)
    assert matrix == {
        "include": [
            {
                "leg": "source",
                "ref": "busybox:1.37.0",
                "slug": "busybox",
                "origin": "third-party",
                "own-floating": False,
            },
            {
                "leg": "source",
                "ref": OWN + "operator:latest",
                "slug": "ghcr-io-nebari-dev-llm-serving-pack-operator",
                "origin": "first-party",
                "own-floating": True,
            },
            {
                "leg": "source",
                "ref": OPENMETER + ":v1@" + DIGEST,
                "slug": "ghcr-io-nebari-dev-openmeter",
                "origin": "first-party",
                "own-floating": False,
            },
        ]
    }


def test_main_env_regex_flag(tmp_path, capsys):
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(textwrap.dedent(ENV_DOC))
    code = main(
        [
            "collect_chart_images.py",
            "--rendered", str(rendered),
            "--leg", "released",
            "--env-regex", "^NOMATCH$",
        ]
    )
    assert code == 0
    refs = [e["ref"] for e in json.loads(capsys.readouterr().out)["include"]]
    assert refs == ["op:1"]


@pytest.mark.parametrize(
    "content, args, code",
    [
        pytest.param("", ["--leg", "source"], 1, id="empty-render"),
        pytest.param(
            "kind: ConfigMap\ndata:\n  a: b\n", ["--leg", "source"], 1, id="no-images"
        ),
        pytest.param(
            "key: [unclosed\n  - : :\n", ["--leg", "source"], 1, id="not-yaml"
        ),
        pytest.param(textwrap.dedent(DEPLOYMENT), [], 2, id="missing-leg"),
        pytest.param(
            textwrap.dedent(DEPLOYMENT), ["--leg", "other"], 2, id="bad-leg"
        ),
        pytest.param(
            textwrap.dedent(DEPLOYMENT),
            ["--leg", "source", "--env-regex", "("],
            2,
            id="bad-env-regex",
        ),
    ],
)
def test_exit_codes(tmp_path, content, args, code, capsys):
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(content)
    assert main(["collect_chart_images.py", "--rendered", str(rendered), *args]) == code
    if code != 0:
        assert capsys.readouterr().out == ""


def test_missing_rendered_is_usage_error():
    assert main(["collect_chart_images.py", "--leg", "source"]) == 2


@pytest.mark.parametrize(
    "extra, slug, refs",
    [
        pytest.param(
            ["redis:7.4.7", "redis:7.4.12"],
            "redis",
            ["redis:7.4.7", "redis:7.4.12"],
            id="same-repo-two-tags",
        ),
        pytest.param(
            ["ghcr.io/a-b/c:1", "ghcr.io/a/b-c:1"],
            "ghcr-io-a-b-c",
            ["ghcr.io/a-b/c:1", "ghcr.io/a/b-c:1"],
            id="two-repos-one-slug",
        ),
    ],
)
def test_slug_collision_exits_1(tmp_path, capsys, extra, slug, refs):
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(textwrap.dedent(DEPLOYMENT))
    args = ["collect_chart_images.py", "--rendered", str(rendered), "--leg", "source"]
    for ref in extra:
        args += ["--extra-image", ref]
    assert main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = [l for l in captured.err.splitlines() if slug in l]
    assert len(lines) == 1
    assert all(ref in lines[0] for ref in refs)


def test_non_utf8_render_is_not_yaml(tmp_path, capsys):
    rendered = tmp_path / "rendered.yaml"
    rendered.write_bytes(b"image: \xff\xfe\x80\n")
    code = main(
        ["collect_chart_images.py", "--rendered", str(rendered), "--leg", "source"]
    )
    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not YAML" in captured.err


UNSAFE_REFS = [
    pytest.param("redis:7\n::set-output name=result::clean", id="newline"),
    pytest.param("redis:7\tx", id="tab"),
    pytest.param("redis 7", id="space"),
    pytest.param("redis:7\x1b[31m", id="control-char"),
    pytest.param("redis:7\x00", id="nul"),
    pytest.param("--config=x", id="leading-dash"),
    pytest.param("redis:7\u202e", id="bidi-override"),
    pytest.param("re\u200bdis:7", id="zero-width-space"),
]


def _render_with(tmp_path, ref, via):
    rendered = tmp_path / "rendered.yaml"
    if via == "extra":
        rendered.write_text(textwrap.dedent(DEPLOYMENT))
        return rendered, ["--extra-image=" + ref]
    doc = {"kind": "Pod", "spec": {"containers": [{"name": "c", "image": "busybox:1"}]}}
    if via == "image":
        doc["spec"]["containers"].append({"name": "bad", "image": ref})
    elif via == "env":
        doc["spec"]["containers"][0]["env"] = [{"name": "X_IMAGE", "value": ref}]
    elif via == "imageName":
        doc["spec"]["imageName"] = ref
    rendered.write_text(json.dumps(doc))  # JSON is YAML and escapes control chars
    return rendered, []


@pytest.mark.parametrize("via", ["image", "env", "imageName", "extra"])
@pytest.mark.parametrize("ref", UNSAFE_REFS)
def test_unsafe_ref_exits_1(tmp_path, capsys, ref, via):
    rendered, extra = _render_with(tmp_path, ref, via)
    args = ["collect_chart_images.py", "--rendered", str(rendered), "--leg", "source"]
    assert main(args + extra) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    err_lines = captured.err.splitlines()
    assert len(err_lines) == 1
    assert repr(ref) in err_lines[0]


@pytest.mark.parametrize("via", ["image", "env", "imageName", "extra"])
def test_safe_ref_still_accepted(tmp_path, capsys, via):
    ref = "ghcr.io/nebari-dev/openmeter:v1.0.0-beta.239-nebari.1@" + DIGEST
    rendered, extra = _render_with(tmp_path, ref, via)
    args = ["collect_chart_images.py", "--rendered", str(rendered), "--leg", "source"]
    assert main(args + extra) == 0
    refs = [e["ref"] for e in json.loads(capsys.readouterr().out)["include"]]
    assert ref in refs


def test_one_line_per_unsafe_ref(tmp_path, capsys):
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(textwrap.dedent(DEPLOYMENT))
    args = ["collect_chart_images.py", "--rendered", str(rendered), "--leg", "source",
            "--extra-image", "a b", "--extra-image=-x"]
    assert main(args) == 1
    assert len(capsys.readouterr().err.splitlines()) == 2


@pytest.mark.parametrize("ref", UNSAFE_REFS)
def test_slug_mode_rejects_unsafe_ref(capsys, ref):
    assert main(["collect_chart_images.py", "--slug=" + ref]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert repr(ref) in captured.err
    assert len(captured.err.splitlines()) == 1


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["--slug", "--config=x"], id="slug"),
        pytest.param(
            ["--rendered", "r.yaml", "--leg", "source", "--extra-image", "-x"],
            id="extra-image",
        ),
    ],
)
def test_separated_dash_value_is_usage_error(capsys, args):
    # argparse reads a separated value starting with "-" as an option.
    assert main(["collect_chart_images.py", *args]) == 2
    assert capsys.readouterr().out == ""
