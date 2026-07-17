# AGENTS.md

This file provides guidance to AI coding agents working in this repository.

## What this repository is

`nebari-dev/.github` is the org-level meta-repository for the Nebari-dev GitHub
organization. It is not an application. It serves two distinct roles:

1. **Community health file source of truth** - shared issue/PR templates,
   `LICENSE`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` live here and are
   automatically synced out to the org's other repos.
2. **Reusable workflow library** - several `workflow_call` workflows under
   `.github/workflows/` are invoked by other repos in the org.

`profile/README.md` renders as the organization's public profile page on
github.com/nebari-dev.

## The two data flows (the key thing to understand)

### File sync (outbound push)

- `.github/workflows/sync-issue-templates.yaml` runs on push to `main` when
  `.github/*`, `CONTRIBUTING.md`, or `LICENSE` change (also `workflow_dispatch`).
  It uses `BetaHuhn/repo-file-sync-action` and opens PRs in downstream repos.
- `.github/sync.yml` is the mapping config: groups of `source -> dest` files,
  each with a list of destination repos. Different repos get different variants
  (e.g. `nebari` gets `PULL_REQUEST_TEMPLATE_simple.md` renamed to
  `PULL_REQUEST_TEMPLATE.md` plus nebari-only templates; `governance` gets
  `RFD.md`; docs repos get doc-specific issue config).
- **Consequence:** editing any shared file here changes downstream repos on the
  next merge to `main`. Before editing an issue/PR template, `LICENSE`,
  `CONTRIBUTING.md`, or `CODE_OF_CONDUCT.md`, check `.github/sync.yml` to see
  which repos it propagates to.
- Requires the `GH_PAT` secret (a bot PAT owned by nebari-sensei).

### Reusable workflows (inbound call)

Other repos call these via `uses: nebari-dev/.github/.github/workflows/<file>@main`:

- `sync-project-priority.yaml` - adds an issue/PR to a Projects v2 board and syncs
  a `Priority` single-select field from `priority: <level>` labels. Callers must
  trigger the PR path on `pull_request_target`, not `pull_request`, so fork PRs
  still receive the PAT secret. Safe because the workflow checks out no PR code:
  it only reads event metadata and calls the GitHub GraphQL API.
  `clearProjectV2ItemFieldValue` supports issues only, so PRs with no priority
  label skip the clear step.
- `pack-build-image.yaml` - builds and pushes a Nebari "software pack" container
  image to both GHCR (`ghcr.io/<owner>/<repo>/<image>`) and Quay
  (`quay.io/nebari/<repo>-<image>`). Pass `push: false` for `pull_request` builds.
- `pack-release.yaml` - releases a Helm chart: reads the version from
  `<chart-path>/Chart.yaml`, skips if the `<chart-name>-<version>` GitHub Release
  already exists (idempotent re-runs), pins image tag values to `sha-<short-sha>`
  of the release commit (working copy only, never committed back), runs
  `helm package`, creates the GitHub Release, then syncs the chart to
  `nebari-dev/helm-repository` (which performs the actual OCI push to quay).
  Callers must also build images for the release commit's sha.

The `README.md` documents the caller-side invocation snippets. Keep them in sync
when changing a reusable workflow's inputs or secrets.

## Scripts

`.github/scripts/pin_image_tags.py` rewrites image `tag` leaves in a chart's
`values.yaml` (dotted paths like `operator.image.tag`) to a given sha tag. It uses
`ruamel.yaml` round-trip mode to preserve comments, quoting, and key order, and
raises `KeyError` (failing the release) if any path segment is missing rather than
silently no-oping. Called by `pack-release.yaml`, which checks out this repo's own
scripts via `job.workflow_repository` / `job.workflow_sha`.

## Common commands

```bash
# pre-commit (prettier + codespell + whitespace/EOF fixers)
pre-commit install
pre-commit run --all-files

# run the Python script tests (needs ruamel.yaml + pytest)
python -m pip install "ruamel.yaml==0.19.1" pytest
python -m pytest .github/scripts/                             # all tests
python -m pytest .github/scripts/test_pin_image_tags.py -v    # single file

# lint the reusable workflows locally (as CI does)
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint \
  .github/workflows/pack-build-image.yaml \
  .github/workflows/pack-release.yaml \
  .github/workflows/lint-test.yaml
```

CI (`.github/workflows/lint-test.yaml`) runs `actionlint` and `pytest` on push/PR.

## Conventions and gotchas

- **prettier formatting is load-bearing.** The prettier pre-commit hook keeps
  YAML/markdown formatting identical to what downstream repos expect so synced
  files don't trip prettier checks there. Always run it before committing changes
  to synced files.
- **actionlint is intentionally scoped.** `lint-test.yaml` lints only
  `pack-build-image.yaml`, `pack-release.yaml`, and itself. The older `sync-*`
  workflows have known pre-existing findings (a floating `actions/checkout@v3`
  pin, shellcheck info notes) and are excluded on purpose. Don't widen the scope
  without first cleaning up those findings.
- **Action pin style differs by era.** The newer pack-*/lint-test workflows pin
  actions to full SHAs with version comments; the older sync-* workflows use
  floating tags. Match the surrounding file, and prefer SHA pins for new work.
- `.github/actionlint.yaml` suppresses two false positives for the documented
  `job.workflow_repository` / `job.workflow_sha` context properties that
  actionlint does not yet know about.
- All Nebari projects use BSD-3-Clause (`LICENSE`), synced from here.
