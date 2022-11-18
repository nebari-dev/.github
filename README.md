# .github

[![Sync Issue Templates](https://github.com/nebari-dev/.github/actions/workflows/sync-issue-templates.yaml/badge.svg)](https://github.com/nebari-dev/.github/actions/workflows/sync-issue-templates.yaml)

This is a meta-repository that defines some shared files for the repositories under the `Nebari-dev` organization.

## :file_folder: Files in this repository

Below is a quick list of what you'll find in this repository:

- `.github/ISSUE_TEMPLATE/`: Issue templates for other repositories. When these files are changed, they are automatically synced to our other repositories via [this GitHub action](.github/workflows/sync-issue-templates.yaml).
- `.github/PULL_REQUEST_TEMPLATE.md`: Pull request templates for other repositories. When these files are changed, they are automatically synced to our other repositories via [this GitHub action](.github/workflows/sync-pull-request-templates.yaml).
- `LICENSE`: All of our projects are under a BSD-3 clause license, this is automatically synced to our other repositories via [this GitHub action](.github/workflows/sync-pull-request-templates.yaml).
- `CONTRIBUTING.md`: Base contributing guidelines for all of our projects, this is automatically synced to our other repositories via [this GitHub action](.github/workflows/sync-pull-request-templates.yaml).

> **Warning**
> The syncing action requires a Personal Authentication Token (PAT) which is currently set up through [Nebari-sensei](https://github.com/nebari-sensei)

## :broom: Pre-commit hooks

This repository uses the `prettier` pre-commit hook to standardize our YAML and markdown structure.
This ensures that when we sync files to other repositories, we do not create conflicts with `prettier` checks in each repository.
To install and run it, use these commands from the repository root:

```bash
# install the pre-commit hooks
pre-commit install

# run the pre-commit hooks
pre-commit run --all-files
```

## :link: References

- See <https://help.github.com/en/articles/creating-a-default-community-health-file-for-your-organization> for more details
