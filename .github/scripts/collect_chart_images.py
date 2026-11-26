#!/usr/bin/env python3
"""Collect the container images a rendered Helm chart deploys.

Used by pack-scan-images.yaml: the chart is rendered with `helm template`,
and this script turns the render into the GitHub Actions matrix of images
to scan, one job per image.

Usage:
    python collect_chart_images.py --rendered FILE --leg {source,released}
        [--env-regex RE] [--extra-image REF]... [--own-prefix PREFIX]
        [--first-party-prefix PREFIX]...
    python collect_chart_images.py --slug REF

Images are found by structure, not by kind:
  - every "image" of an entry in a containers, initContainers or
    ephemeralContainers list, at any depth
  - every env entry whose "name" matches --env-regex (default "_IMAGE$")
  - every "imageName" string (for example a CloudNativePG Cluster)
  - each --extra-image
References are de-duplicated, keeping the first occurrence.

Each matrix entry carries:
  - slug: the repository (the reference without tag and digest) with every
    run of non-alphanumeric characters replaced by "-", so it is stable
    across tag changes
  - origin: "first-party" when the repository is under --own-prefix or a
    --first-party-prefix (case-insensitive, on a path boundary), else
    "third-party"
  - own-floating: true only for a reference under --own-prefix whose tag is
    "latest" or absent and that carries no digest

--slug REF prints the slug of REF's repository, the same value the matrix
uses for any reference to that repository.

Exit codes: 0 matrix JSON on stdout, 1 nothing found, the input is not
UTF-8 YAML, or two references in the leg share a slug (one stderr line per
slug, naming the references), 2 usage error or unreadable file.
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

CONTAINER_LISTS = ("containers", "initContainers", "ephemeralContainers")
DEFAULT_ENV_REGEX = r"_IMAGE$"
FLOATING_TAG = "latest"


@dataclass(frozen=True)
class ImageEntry:
    leg: str  # "source" | "released"
    ref: str  # exactly as rendered
    slug: str  # repository with every non-alphanumeric run replaced by "-"
    origin: str  # "first-party" | "third-party"
    own_floating: bool  # own prefix, tag latest or none, no digest


def _split_ref(ref: str) -> tuple[str, str | None, str | None]:
    """Return (repository, tag, digest) of an image reference.

    The digest is everything after the first "@". The tag is what follows
    the last ":" that comes after the last "/", so a registry port
    (localhost:5000/x) is not mistaken for a tag.
    """
    name, at, digest = ref.partition("@")
    slash = name.rfind("/")
    colon = name.rfind(":")
    if colon > slash:
        repository, tag = name[:colon], name[colon + 1 :]
    else:
        repository, tag = name, None
    return repository, tag or None, (digest if at else None)


def slugify(repository: str) -> str:
    """Replace every run of non-alphanumeric characters with "-"."""
    return re.sub(r"[^A-Za-z0-9]+", "-", repository)


def _under(repository: str, prefix: str) -> bool:
    base = prefix.rstrip("/").lower()
    if not base:
        return False
    repository = repository.lower()
    return repository == base or repository.startswith(base + "/")


def _walk(node: Any, env_regex: re.Pattern[str], found: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in CONTAINER_LISTS and isinstance(value, list):
                for container in value:
                    if isinstance(container, dict):
                        _add(container.get("image"), found)
            elif key == "env" and isinstance(value, list):
                for var in value:
                    if (
                        isinstance(var, dict)
                        and isinstance(var.get("name"), str)
                        and env_regex.search(var["name"])
                    ):
                        _add(var.get("value"), found)
            elif key == "imageName":
                _add(value, found)
            _walk(value, env_regex, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, env_regex, found)


def _add(value: Any, found: list[str]) -> None:
    if isinstance(value, str) and value.strip():
        found.append(value.strip())


def collect(
    documents: Iterable[Any], env_regex: re.Pattern[str], extra_images: Sequence[str]
) -> list[str]:
    """Return every image reference in the documents, de-duplicated in order."""
    found: list[str] = []
    for document in documents:
        _walk(document, env_regex, found)
    for extra in extra_images:
        _add(extra, found)
    return list(dict.fromkeys(found))


def build_entries(
    refs: Sequence[str],
    leg: str,
    own_prefix: str,
    first_party_prefixes: Sequence[str],
) -> list[ImageEntry]:
    entries = []
    for ref in refs:
        repository, tag, digest = _split_ref(ref)
        own = _under(repository, own_prefix)
        first_party = own or any(_under(repository, p) for p in first_party_prefixes)
        entries.append(
            ImageEntry(
                leg=leg,
                ref=ref,
                slug=slugify(repository),
                origin="first-party" if first_party else "third-party",
                own_floating=own
                and digest is None
                and (tag is None or tag == FLOATING_TAG),
            )
        )
    return entries


def slug_collisions(entries: Sequence[ImageEntry]) -> list[str]:
    """Return one message per slug shared by more than one entry of a leg."""
    refs_by_slug: dict[tuple[str, str], list[str]] = {}
    for e in entries:
        refs_by_slug.setdefault((e.leg, e.slug), []).append(e.ref)
    return [
        f"slug {slug!r} in leg {leg!r} is shared by: {', '.join(refs)}"
        for (leg, slug), refs in refs_by_slug.items()
        if len(refs) > 1
    ]


def to_matrix(entries: Sequence[ImageEntry]) -> dict[str, list[dict[str, Any]]]:
    return {
        "include": [
            {
                "leg": e.leg,
                "ref": e.ref,
                "slug": e.slug,
                "origin": e.origin,
                "own-floating": e.own_floating,
            }
            for e in entries
        ]
    }


def _regex(text: str) -> re.Pattern[str]:
    try:
        return re.compile(text)
    except re.error as exc:
        raise argparse.ArgumentTypeError(f"invalid regex {text!r}: {exc}")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="collect_chart_images.py")
    parser.add_argument("--slug", metavar="REF")
    parser.add_argument("--rendered", metavar="FILE")
    parser.add_argument("--leg", choices=("source", "released"))
    parser.add_argument(
        "--env-regex", type=_regex, default=re.compile(DEFAULT_ENV_REGEX)
    )
    parser.add_argument("--extra-image", action="append", default=[])
    parser.add_argument("--own-prefix", default="")
    parser.add_argument("--first-party-prefix", action="append", default=[])
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return 2 if exc.code else 0

    if args.slug is not None:
        print(slugify(_split_ref(args.slug)[0]))
        return 0
    if args.rendered is None or args.leg is None:
        print(
            "collect_chart_images: error: --rendered and --leg are required",
            file=sys.stderr,
        )
        return 2

    try:
        with open(args.rendered, encoding="utf-8") as f:
            documents = list(YAML(typ="safe").load_all(f))
    except OSError as exc:
        print(f"collect_chart_images: error: {exc}", file=sys.stderr)
        return 2
    except (YAMLError, UnicodeDecodeError) as exc:
        print(f"collect_chart_images: error: not YAML: {exc}", file=sys.stderr)
        return 1

    refs = collect(documents, args.env_regex, args.extra_image)
    if not refs:
        print("collect_chart_images: error: no images found", file=sys.stderr)
        return 1
    entries = build_entries(
        refs, args.leg, args.own_prefix, args.first_party_prefix
    )
    collisions = slug_collisions(entries)
    for message in collisions:
        print(f"collect_chart_images: error: {message}", file=sys.stderr)
    if collisions:
        return 1
    print(json.dumps(to_matrix(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
