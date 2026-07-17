# Verifying Nebari pack artifacts

Nebari software packs (for example `llm-serving-pack`) build and release their
artifacts through this repository's reusable workflows:

- [`.github/workflows/pack-build-image.yaml`](.github/workflows/pack-build-image.yaml)
  builds and pushes container images to both GHCR and Quay, then signs and
  attests each one with the
  [`sign-oci`](.github/actions/sign-oci/action.yml) action.
- [`.github/workflows/pack-release.yaml`](.github/workflows/pack-release.yaml)
  packages the pack's Helm chart into a `.tgz`, signs it with the
  [`sign-blob`](.github/actions/sign-blob/action.yml) action, and attaches the
  `.tgz`, its `.sigstore.json` bundle, and its `.spdx.json` SBOM to the
  GitHub Release.

All signing is keyless (Sigstore/Fulcio, backed by the GitHub Actions OIDC
token), so there is no public key to distribute. Verification instead checks
that the Fulcio certificate was issued to the expected workflow identity by
the expected OIDC issuer. This doc gives copy-pasteable commands for that.

> This covers three artifact types: a pack's container images, its release
> `.tgz`, and the canonical OCI Helm chart published to `quay.io/nebari/charts`
> (which has a different signer identity - see
> [The canonical OCI Helm chart](#the-canonical-oci-helm-chart) below).

## Prerequisites

- [`cosign`](https://docs.sigstore.dev/system_config/installation/) v2 or later.
- [`gh`](https://cli.github.com/) (GitHub CLI) with the `attestation` command,
  authenticated via `gh auth login`.
- If the registry requires auth to pull, log in first (`docker login ghcr.io`
  / `docker login quay.io`).

## Why the identity points at this repo, not the pack's

Both reusable workflows run their signing steps directly (via the `sign-oci`
and `sign-blob` composite actions), so the OIDC token minted for that job is
the one GitHub issues for **the reusable workflow that owns the job's
steps** - not the pack repo that called it. GitHub's OIDC token includes a
`job_workflow_ref` claim specifically for this case:

> "For jobs using a reusable workflow, the ref path to the reusable workflow."
>
> - [GitHub Actions OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)

> "If a job is part of a reusable workflow, the token will include the
> standard claims that contain information about the calling workflow, and
> will also include a custom claim called `job_workflow_ref` that contains
> information about the called workflow."
>
> - [Using OpenID Connect with reusable workflows](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows)

Fulcio, in turn, maps that claim onto the certificate's Subject Alternative
Name via the "Build Signer URI" extension:

> "Reference to specific build instructions that are responsible for
> signing. SHOULD be fully qualified. MAY be the same as Build Config URI."
> Example: `https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@v1.4.0`
>
> - [Fulcio OID information](https://github.com/sigstore/fulcio/blob/main/docs/oid-info.md)

and a maintainer discussion on `sigstore/cosign` confirms this in practice for
reusable workflows: the certificate's SAN reflects the reusable workflow's own
ref (its "Build Signer URI"), while the caller's workflow ref is carried
separately (its "Build Config URI") -
[sigstore/cosign discussion #2936](https://github.com/sigstore/cosign/discussions/2936).

In practice this means: no matter which pack repo produced an image or chart,
the certificate identity you verify against is always a path inside
`nebari-dev/.github` - `pack-build-image.yaml` for images,
`pack-release.yaml` for the chart `.tgz` - at whatever ref that pack pinned in
its `uses:` line (for example `@v1`, which resolves to `refs/tags/v1`).

> **Confirm before relying on this in production.** No pack has published a
> real signed artifact yet (that's journey 8, exercised post-merge by the
> first real `llm-serving-pack` release). The claim/OID mechanics above are
> documented behavior, but the exact literal identity string - in particular
> whether the ref resolves to `refs/tags/v1` versus a commit SHA - should be
> confirmed against that first real release before being treated as gospel.
> See [Confirming the exact identity](#confirming-the-exact-identity) for how
> to check.

## 1. Container images (GHCR or Quay)

Both registries are signed the same way, by the same workflow
(`pack-build-image.yaml`), so the identity regexp is identical regardless of
which registry you pulled from. Always verify by digest, not by tag.

```bash
IMAGE="ghcr.io/nebari-dev/llm-serving-pack/operator"   # or: quay.io/nebari/llm-serving-pack-operator
DIGEST="sha256:<digest>"

cosign verify "${IMAGE}@${DIGEST}" \
  --certificate-identity-regexp "https://github\.com/nebari-dev/\.github/\.github/workflows/pack-build-image\.yaml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

## 2. Build provenance and SBOM attestations

In addition to the `cosign` signature above, both `sign-oci` and `sign-blob`
generate GitHub artifact attestations (SLSA build provenance and an SPDX
SBOM), verifiable with `gh attestation verify` against the **pack repo**
(that's where the workflow run - and therefore the attestation - is
recorded), not `nebari-dev/.github`.

Because the attestation was generated by this repo's reusable workflow
rather than a workflow defined in the pack repo itself, `--repo` alone is
not enough to pin the signer - you must also pass `--signer-workflow`. Per
the [`gh attestation verify`
manual](https://cli.github.com/manual/gh_attestation_verify):

> Please note: if your attestation was generated via a reusable workflow
> then that reusable workflow is the signer whose identity needs to be
> validated. In this situation, you must use either the
> `--signer-workflow` or the `--signer-repo` flag.

`--signer-workflow` takes `[host/]<owner>/<repo>/<path>/<to>/<workflow>` -
no `@ref` and no `https://` prefix.

Build provenance:

```bash
gh attestation verify oci://ghcr.io/nebari-dev/llm-serving-pack/operator@sha256:<digest> \
  --repo nebari-dev/llm-serving-pack \
  --signer-workflow nebari-dev/.github/.github/workflows/pack-build-image.yaml
```

SBOM (pass the SPDX predicate type explicitly; `gh attestation verify`
otherwise defaults to the SLSA provenance predicate):

```bash
gh attestation verify oci://ghcr.io/nebari-dev/llm-serving-pack/operator@sha256:<digest> \
  --repo nebari-dev/llm-serving-pack \
  --signer-workflow nebari-dev/.github/.github/workflows/pack-build-image.yaml \
  --predicate-type https://spdx.dev/Document/v2.3
```

## 3. Helm chart `.tgz` from a GitHub Release

Every pack release publishes three assets together: the packaged chart, its
`cosign` bundle, and its SBOM. Download all three, then verify the bundle
against the chart before doing anything else with it.

```bash
gh release download llm-serving-pack-1.2.3 \
  --repo nebari-dev/llm-serving-pack \
  --pattern 'llm-serving-pack-1.2.3.tgz*'
# downloads:
#   llm-serving-pack-1.2.3.tgz
#   llm-serving-pack-1.2.3.tgz.sigstore.json
#   llm-serving-pack-1.2.3.tgz.spdx.json

cosign verify-blob \
  --bundle llm-serving-pack-1.2.3.tgz.sigstore.json \
  --certificate-identity-regexp "https://github\.com/nebari-dev/\.github/\.github/workflows/pack-release\.yaml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  llm-serving-pack-1.2.3.tgz
```

The chart also carries the same kind of GitHub attestations as the images
(see [section 2](#2-build-provenance-and-sbom-attestations)), verifiable
against the local file instead of an `oci://` reference. As with the image
attestations, `--signer-workflow` is required - here it points at
`pack-release.yaml`, the reusable workflow that actually signed the chart:

```bash
gh attestation verify llm-serving-pack-1.2.3.tgz --repo nebari-dev/llm-serving-pack \
  --signer-workflow nebari-dev/.github/.github/workflows/pack-release.yaml
gh attestation verify llm-serving-pack-1.2.3.tgz --repo nebari-dev/llm-serving-pack \
  --signer-workflow nebari-dev/.github/.github/workflows/pack-release.yaml \
  --predicate-type https://spdx.dev/Document/v2.3
```

## Confirming the exact identity

Until the first real pack release, treat the regexp above as expected-but-unconfirmed
for the exact ref suffix. Once a real image or chart exists, inspect what was
actually issued rather than trusting this doc blindly:

```bash
# Full certificate details behind a cosign verification, as JSON:
cosign verify "${IMAGE}@${DIGEST}" \
  --certificate-identity-regexp "https://github\.com/nebari-dev/\.github/\.github/workflows/pack-build-image\.yaml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --output json | jq '.[0].optional.Issuer, .[0].optional.Subject'

# Raw attestation claims, as JSON:
gh attestation verify oci://ghcr.io/nebari-dev/llm-serving-pack/operator@sha256:<digest> \
  --repo nebari-dev/llm-serving-pack \
  --signer-workflow nebari-dev/.github/.github/workflows/pack-build-image.yaml \
  --format json | jq .
```

If the observed identity ever differs from what's documented here, this file
is out of date - update it rather than working around it.

## The canonical OCI Helm chart

`pack-release.yaml` signs the chart `.tgz` attached to the pack's GitHub Release
(above). The chart consumers actually `helm install` is a **separate** artifact:
the OCI chart pushed to `quay.io/nebari/charts` by
[`nebari-dev/helm-repository`](https://github.com/nebari-dev/helm-repository)'s
own `release-helm-charts.yml` workflow. That workflow signs and
provenance-attests each OCI chart with the same `sign-oci` action (signature +
SLSA provenance, but **no SBOM** - an SBOM of a Helm chart artifact is not
meaningful).

Because the signing happens inside `helm-repository`'s workflow, the OCI chart's
signer identity is `release-helm-charts.yml` in **that** repo - distinct from the
`pack-build-image.yaml` / `pack-release.yaml` identities above. Verify the
signature:

```bash
cosign verify quay.io/nebari/charts/<chart>:<version> \
  --certificate-identity-regexp "https://github.com/nebari-dev/helm-repository/\.github/workflows/release-helm-charts\.yml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

And the build provenance:

```bash
gh attestation verify oci://quay.io/nebari/charts/<chart>@sha256:<digest> \
  --repo nebari-dev/helm-repository \
  --signer-workflow nebari-dev/helm-repository/.github/workflows/release-helm-charts.yml
```

The identity uses `@.*` because the exact ref depends on how the release ran
(e.g. `@refs/heads/main`); match on the workflow path, not a fixed ref.
