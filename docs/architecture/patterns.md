# Patterns

Conventions confirmed for this repository. New code follows them; a change
that breaks one updates this file in the same PR.

### Reusable pack workflows are `workflow_call` files named `pack-<verb>.yaml` with kebab-case inputs, explicitly named secrets, and per-job least `permissions`
Examples: `.github/workflows/pack-build-image.yaml`, `.github/workflows/pack-release.yaml`. Each job declares only the permissions it needs.
**Confirmed:** 2026-11-26 (spec: llm-serving-pack docs/superpowers/specs/2026-11-26-pack-cve-scanning-and-renovate-design.md)

### Third-party actions are pinned by full commit SHA with a `# vX.Y.Z` comment
A `uses:` of an action outside this repo names a 40-character commit SHA, followed by a comment with the release it corresponds to (or the branch and date when the action has no release), in workflows and composite actions alike. `sync-issue-templates.yaml` still uses a floating `actions/checkout@v3` and predates this pattern.
**Confirmed:** 2026-11-26 (spec: llm-serving-pack docs/superpowers/specs/2026-11-26-pack-cve-scanning-and-renovate-design.md)

### A reusable workflow loads its own repo's composite actions and scripts from a `_dot-github` checkout of `job.workflow_repository@job.workflow_sha`, with matching `actionlint.yaml` ignores
`uses: ./` in a reusable workflow resolves against the caller's workspace, so the workflow checks out this repo at the exact commit the caller invoked into `_dot-github` and uses `./_dot-github/.github/actions/<name>` or `_dot-github/.github/scripts/<name>.py`. actionlint does not know `job.workflow_repository` and `job.workflow_sha` yet, so each such workflow has an ignore entry in `.github/actionlint.yaml`.
**Confirmed:** 2026-11-26 (spec: llm-serving-pack docs/superpowers/specs/2026-11-26-pack-cve-scanning-and-renovate-design.md)

### Helper logic is a Python script in `.github/scripts/<name>.py` with `test_<name>.py` beside it: a pure function plus `main(argv)` returning an exit code, failing loudly on bad input; the `pytest` job in `lint-test.yaml` runs the directory
Examples: `pin_image_tags.py`, `lint_trivyignore.py`. Workflows call the script rather than inlining its logic.
**Confirmed:** 2026-11-26 (spec: llm-serving-pack docs/superpowers/specs/2026-11-26-pack-cve-scanning-and-renovate-design.md)

### Composite actions live in `.github/actions/<name>/action.yml`, take string inputs, and pass inputs to `shell: bash` steps through `env`
Inputs are never interpolated into a `run:` script directly. Examples: `sign-oci`, `sign-blob`, `trivy-scan`.
**Confirmed:** 2026-11-26 (spec: llm-serving-pack docs/superpowers/specs/2026-11-26-pack-cve-scanning-and-renovate-design.md)

### Callers reference `nebari-dev/.github` by a major tag: either floating (`@v1`) or as a commit SHA with a `# v1` comment
A major tag is never moved once callers depend on it; a change callers must opt into ships under a new major tag. See the Tag `v1` entry in `contracts.md`.
**Confirmed:** 2026-11-26 (spec: llm-serving-pack docs/superpowers/specs/2026-11-26-pack-cve-scanning-and-renovate-design.md)
