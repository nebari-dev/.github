#!/usr/bin/env python3
"""Pin container image tags in a Helm values.yaml file to a release tag.

Used by pack-release.yaml to rewrite the working copy of a chart's
values.yaml so that its image tags point at the sha-<short> tag built
for the release commit, right before packaging. The change is made only
in the workflow's working directory; it is never committed back to the
source repository.

Usage:
    python pin_image_tags.py <values-file> <sha-tag> <path1> [<path2> ...]

Each path is a dot-separated key path into the YAML document, e.g.
"operator.image.tag". The walk sets the final key in the path (the
"tag" leaf) to <sha-tag>, leaving every sibling key and comment intact.

Uses ruamel.yaml's round-trip mode so comments, quoting, and key order
in the original file are preserved. Fails loudly (non-zero exit) if any
segment of a path does not exist in the document -- a missing path is
almost always a typo'd values.yaml key and should stop the release
rather than silently no-op.
"""
import sys

from ruamel.yaml import YAML


def pin(values_file, sha_tag, paths):
    """Set the leaf key of each dotted path in values_file to sha_tag.

    Args:
        values_file: path to a values.yaml file, read and rewritten in place.
        sha_tag: the value to assign to each path's leaf key, e.g. "sha-abc1234".
        paths: dotted key paths such as "operator.image.tag". The last
            segment is the leaf that gets set; everything before it is
            walked as nested mapping keys.

    Raises:
        KeyError: if any segment of any path is missing from the document.
    """
    yaml = YAML()
    yaml.preserve_quotes = True

    with open(values_file) as f:
        data = yaml.load(f)

    for dotted in paths:
        keys = dotted.split(".")
        node = data
        for key in keys[:-1]:
            if not isinstance(node, dict) or key not in node:
                raise KeyError(
                    f"path '{dotted}' not found in {values_file}: "
                    f"no key '{key}'"
                )
            node = node[key]

        leaf = keys[-1]
        if not isinstance(node, dict) or leaf not in node:
            raise KeyError(
                f"path '{dotted}' not found in {values_file}: "
                f"no key '{leaf}'"
            )
        node[leaf] = sha_tag

    with open(values_file, "w") as f:
        yaml.dump(data, f)


def main(argv):
    if len(argv) < 4:
        print(
            "usage: pin_image_tags.py <values-file> <sha-tag> <path1> [<path2> ...]",
            file=sys.stderr,
        )
        return 2

    values_file, sha_tag, *paths = argv[1:]
    try:
        pin(values_file, sha_tag, paths)
    except Exception as exc:
        print(f"pin_image_tags: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
