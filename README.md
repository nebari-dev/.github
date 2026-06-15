# .github

[![Sync Issue Templates](https://github.com/nebari-dev/.github/actions/workflows/sync-issue-templates.yaml/badge.svg)](https://github.com/nebari-dev/.github/actions/workflows/sync-issue-templates.yaml)

This is a meta-repository that defines some shared files for the repositories under the `Nebari-dev` organization.

## :file_folder: Files in this repository

Below is a quick list of what you'll find in this repository:

- `.github/ISSUE_TEMPLATE/`: Issue templates for other repositories. When these files are changed, they are automatically synced to our other repositories via [this GitHub action](.github/workflows/sync-issue-templates.yaml).
- `.github/PULL_REQUEST_TEMPLATE.md`: Pull request templates for other repositories. When these files are changed, they are automatically synced to our other repositories via [this GitHub action](.github/workflows/sync-issue-templates.yaml).
- `LICENSE`: All of our projects are under a BSD-3 clause license, this is automatically synced to our other repositories via [this GitHub action](.github/workflows/sync-issue-templates.yaml).
- `CONTRIBUTING.md`: Base contributing guidelines for all of our projects, this is automatically synced to our other repositories via [this GitHub action](.github/workflows/sync-issue-templates.yaml).

> **Note**
> The file-syncing above is all driven by the single [`sync-issue-templates.yaml`](.github/workflows/sync-issue-templates.yaml) workflow, configured via [`.github/sync.yml`](.github/sync.yml) using [`BetaHuhn/repo-file-sync-action`](https://github.com/BetaHuhn/repo-file-sync-action).

## :arrows_counterclockwise: Reusable workflows

- [`.github/workflows/sync-project-priority.yaml`](.github/workflows/sync-project-priority.yaml): Reusable workflow (`workflow_call`) that adds an issue/PR to a Projects v2 board and syncs a `Priority` single-select field from `priority: <level>` labels. Caller repos invoke it on their issue and pull request events, passing the project's GraphQL node ID and a PAT with `project: write` scope:

  ```yaml
  on:
    issues:
      types: [opened, labeled, unlabeled]
    pull_request_target:
      types: [opened, labeled, unlabeled]
  jobs:
    sync:
      uses: nebari-dev/.github/.github/workflows/sync-project-priority.yaml@main
      with:
        project-id: <PVT_…>
      secrets:
        token: ${{ secrets.ADD_TO_PROJECT_PAT }}
  ```

  > **Note**
  > For the PR trigger, callers must use `pull_request_target`, **not** `pull_request`. PRs opened from forks do not receive secrets under `pull_request`, so the PAT is empty and the job fails. `pull_request_target` runs in the base repo's context, making the secret available. This is safe because the workflow checks out no PR code — it only reads trusted event metadata and calls the GitHub API.

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
