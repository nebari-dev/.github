#!/usr/bin/env python3
"""Decide whether a pinned third-party image has something newer on its line.

Run by the image-scan workflow for a third-party image that has findings:
the scan job fails only when a newer release exists on the image's line,
or the digest the registry serves for the pinned tag has moved. Otherwise
the findings are reported and the job passes.

Usage:
    python check_image_freshness.py REF --tags-file FILE [--tag-digest sha256:...]

REF is the image reference exactly as rendered (e.g.
"docker.io/library/redis:7.4.7@sha256:..."). FILE lists the repository's
tags, one per line. --tag-digest is the digest the registry serves today
for the pinned tag.

Rules:
  - a tag is a version when it matches
    v?N(.N)*[-alpha|beta|rc.N][-variant]; anything else (latest, main,
    sha-..., signatures, commit hashes) is ignored
  - two tags are comparable only when their variant ("alpine",
    "system-trixie", "" for none) is equal and they are on the same line:
    the same major for 1.x and above, the same 0.y for 0.x
  - a pre-release tag (alpha/beta/rc.N) counts only when the pinned tag is
    a pre-release of the same release and train; stable tags above the
    pinned tag count for any pin, so a stable vX.Y.Z beats vX.Y.Z-beta.N
  - "v1.2.3" and "1.2.3" compare by their numbers; trailing ".0"
    components do not make a tag newer (18.4 == 18.4.0)
  - when a newer tag and a moved digest both exist, the newer tag is
    reported

Exit codes: 0 nothing newer, 1 something newer (one line on stdout naming
the newest counted tag, or "digest <old> -> <new>"), 2 usage error, REF
without a version tag, or an unreadable tags file.
"""
import argparse
import re
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence

_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+)*)(?:-([a-z]+)\.(\d+))?(?:-(.+))?$")
_TRAINS = frozenset({"alpha", "beta", "rc"})


@dataclass(frozen=True)
class ParsedTag:
    raw: str                           # the tag as listed, e.g. "25.12.3-alpine"
    release: tuple[int, ...]           # (25, 12, 3)
    pre: tuple[str, int] | None        # ("beta", 234) for v1.0.0-beta.234; None for a stable release
    variant: str                       # "alpine", "system-trixie", "" when there is none


def parse_tag(tag: str) -> ParsedTag | None:
    """Parse a version tag; None for tags that are not versions."""
    match = _TAG_RE.match(tag)
    if not match:
        return None
    numbers, train, train_number, variant = match.groups()
    release = tuple(int(n) for n in numbers.split("."))
    variant = variant or ""
    pre = None
    if train is not None:
        if train in _TRAINS:
            pre = (train, int(train_number))
        else:
            # "1.2.3-foo.4": not a pre-release train, so it is part of the variant
            dotted = f"{train}.{train_number}"
            variant = f"{dotted}-{variant}" if variant else dotted
    return ParsedTag(tag, release, pre, variant)


def _split_ref(ref: str) -> tuple[str, str | None]:
    """Split REF into (name[:tag], digest or None)."""
    name, at, digest = ref.partition("@")
    return name, (digest if at else None)


def pinned_tag(ref: str) -> ParsedTag:
    """The version tag of REF; raises ValueError when REF has no version tag."""
    name, _ = _split_ref(ref)
    # Only the last path segment can carry the tag: a colon earlier is a
    # registry port (localhost:5000/x).
    last = name.rpartition("/")[2]
    _, colon, tag = last.partition(":")
    if not colon or not tag:
        raise ValueError(f"{ref}: no tag")
    parsed = parse_tag(tag)
    if parsed is None:
        raise ValueError(f"{ref}: tag {tag!r} is not a version")
    return parsed


def pinned_digest(ref: str) -> str | None:
    """The sha256:... after @ in REF, or None when REF carries no digest."""
    return _split_ref(ref)[1] or None


def _numbers(release: tuple[int, ...]) -> tuple[int, ...]:
    # Drop trailing zeros so 18.4 and 18.4.0 compare equal.
    end = len(release)
    while end > 1 and release[end - 1] == 0:
        end -= 1
    return release[:end]


def _line(release: tuple[int, ...]) -> tuple[int, ...]:
    if release[0] >= 1:
        return release[:1]
    return (release + (0,))[:2]


def _order(tag: ParsedTag) -> tuple:
    # A stable release sorts above every pre-release of the same numbers.
    if tag.pre is None:
        return (_numbers(tag.release), 1, 0)
    return (_numbers(tag.release), 0, tag.pre[1])


def _counts(pinned: ParsedTag, candidate: ParsedTag) -> bool:
    if candidate.variant != pinned.variant:
        return False
    if _line(candidate.release) != _line(pinned.release):
        return False
    if candidate.pre is not None:
        if pinned.pre is None or candidate.pre[0] != pinned.pre[0]:
            return False
        if _numbers(candidate.release) != _numbers(pinned.release):
            return False
    return _order(candidate) > _order(pinned)


def newer_release(pinned: ParsedTag, tags: Iterable[str]) -> str | None:
    """The newest comparable tag above PINNED on its line, or None."""
    pin_prefixed = pinned.raw.startswith("v")
    best = None
    best_key = None
    for tag in tags:
        candidate = parse_tag(tag.strip())
        if candidate is None or not _counts(pinned, candidate):
            continue
        # Ties on the numbers ("v0.7.1" and "0.7.1") prefer the pin's style.
        key = (_order(candidate), candidate.raw.startswith("v") == pin_prefixed, candidate.raw)
        if best_key is None or key > best_key:
            best, best_key = candidate.raw, key
    return best


def digest_moved(pinned: str | None, current: str | None) -> bool:
    """True only when both digests are set and differ."""
    return bool(pinned) and bool(current) and pinned != current


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="check_image_freshness.py")
    parser.add_argument("ref")
    parser.add_argument("--tags-file", required=True)
    parser.add_argument("--tag-digest", default=None)
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return 2 if exc.code else 0

    try:
        pinned = pinned_tag(args.ref)
    except ValueError as exc:
        print(f"check_image_freshness: error: {exc}", file=sys.stderr)
        return 2

    try:
        with open(args.tags_file) as f:
            tags = [line.strip() for line in f if line.strip()]
    except OSError as exc:
        print(f"check_image_freshness: error: {exc}", file=sys.stderr)
        return 2

    newer = newer_release(pinned, tags)
    if newer is not None:
        print(f"{args.ref}: newer release {newer} on the line of {pinned.raw}")
        return 1
    old = pinned_digest(args.ref)
    if digest_moved(old, args.tag_digest):
        print(f"{args.ref}: digest {old} -> {args.tag_digest}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
