"""Tests for pin_image_tags.py.

Run with: python -m pytest .github/scripts/test_pin_image_tags.py -v
"""
import textwrap

import pytest
from ruamel.yaml import YAML

from pin_image_tags import pin

SAMPLE_VALUES = textwrap.dedent(
    """\
    operator:
      image:
        repository: quay.io/nebari/llm-d-operator
        tag: latest  # bumped by CI on each release
      replicas: 1

    worker:
      image:
        repository: quay.io/nebari/llm-d-worker
        tag: latest
    """
)


@pytest.fixture
def values_file(tmp_path):
    path = tmp_path / "values.yaml"
    path.write_text(SAMPLE_VALUES)
    return path


def _load(values_file):
    yaml = YAML()
    with open(values_file) as f:
        return yaml.load(f)


def test_pin_sets_tag(values_file):
    pin(str(values_file), "sha-abc1234", ["operator.image.tag"])

    data = _load(values_file)
    assert data["operator"]["image"]["tag"] == "sha-abc1234"
    # untouched path is left alone
    assert data["worker"]["image"]["tag"] == "latest"


def test_pin_preserves_comment(values_file):
    pin(str(values_file), "sha-abc1234", ["operator.image.tag"])

    text = values_file.read_text()
    # ruamel may re-flow the whitespace before an inline comment when the
    # value's length changes, but the comment text itself must survive.
    assert "tag: sha-abc1234" in text
    assert "# bumped by CI on each release" in text


def test_pin_multiple_paths(values_file):
    pin(str(values_file), "sha-def5678", ["operator.image.tag", "worker.image.tag"])

    data = _load(values_file)
    assert data["operator"]["image"]["tag"] == "sha-def5678"
    assert data["worker"]["image"]["tag"] == "sha-def5678"


def test_missing_leaf_raises(values_file):
    with pytest.raises(Exception):
        pin(str(values_file), "sha-abc1234", ["operator.image.missing"])


def test_missing_intermediate_key_raises(values_file):
    with pytest.raises(Exception):
        pin(str(values_file), "sha-abc1234", ["nonexistent.image.tag"])


def test_original_file_unchanged_on_partial_success_is_not_guaranteed(values_file):
    # Documents current behavior: a later path failing does not roll back
    # earlier writes made in-memory before the dump. Since dump only
    # happens once at the end, a failure means no dump happens at all and
    # the file on disk is untouched.
    original = values_file.read_text()
    with pytest.raises(Exception):
        pin(str(values_file), "sha-abc1234", ["operator.image.tag", "nonexistent.path.tag"])
    assert values_file.read_text() == original
