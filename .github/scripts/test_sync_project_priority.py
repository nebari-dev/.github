"""Tests for the "Apply priority from labels" step of sync-project-priority.yaml.

The step is an inline bash script, so these tests extract that exact script out
of the workflow YAML and run it under bash with a stub `gh` on PATH. Testing the
shipped text rather than a copy means the tests cannot drift away from what
actually runs in CI.

Run with: python -m pytest .github/scripts/test_sync_project_priority.py -v
"""
import json
import os
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

WORKFLOW = (
    Path(__file__).resolve().parents[1] / "workflows" / "sync-project-priority.yaml"
)
STEP_NAME = "Apply priority from labels"

# Mirrors the Priority single-select options a board is expected to carry.
OPTIONS = [
    {"id": "opt-critical", "name": "Critical"},
    {"id": "opt-high", "name": "High"},
    {"id": "opt-medium", "name": "Medium"},
    {"id": "opt-low", "name": "Low"},
    {"id": "opt-icebox", "name": "Icebox"},
]

# Label names carry a trailing emoji in the Nebari repos; the step is expected to
# read the priority out of the "priority: <word>" prefix and ignore the rest.
HIGH = "priority: high \U0001f525"
LOW = "priority: low \U0001f331"
DISCUSSION = "needs: discussion \U0001f4ac"
AREA_CI = "area: ci \U0001f527"
# Labels that contain "priority: " but do not start with it. The step anchors
# its match to the start of the label name, so neither is a priority label.
PHRASE = "blocked by priority: high \U0001f525"
HYPHENATED = "de-priority: high \U0001f525"

# A stub `gh` that records every invocation as one JSON line and answers with a
# response shaped like the real GraphQL API, so the step's `--jq` still works.
GH_STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["GH_CALLS"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({"data": {"clearProjectV2ItemFieldValue": {"projectV2Item": {"id": "item"}},
                           "updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "item"}}}}))
"""


def _step_script():
    yaml = YAML()
    with open(WORKFLOW) as fh:
        doc = yaml.load(fh)
    for step in doc["jobs"]["sync"]["steps"]:
        if step.get("name") == STEP_NAME:
            return step["run"]
    raise AssertionError(f"step {STEP_NAME!r} not found in {WORKFLOW}")


def _step_env():
    yaml = YAML()
    with open(WORKFLOW) as fh:
        doc = yaml.load(fh)
    for step in doc["jobs"]["sync"]["steps"]:
        if step.get("name") == STEP_NAME:
            return dict(step.get("env") or {})
    raise AssertionError(f"step {STEP_NAME!r} not found in {WORKFLOW}")


@pytest.fixture
def run_step(tmp_path):
    """Run the real step script and report which GraphQL mutations it issued."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)

    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    calls = tmp_path / "calls.jsonl"

    def _run(labels, *, action, changed_label, is_issue=True, options=OPTIONS):
        (runner_temp / "options.json").write_text(json.dumps(options))
        script = tmp_path / "step.sh"
        script.write_text(_step_script())

        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "GH_CALLS": str(calls),
            "GH_TOKEN": "stub",
            "RUNNER_TEMP": str(runner_temp),
            "PROJECT_ID": "PVT_project",
            "FIELD_ID": "PVTSSF_field",
            "ITEM_ID": "PVTI_item",
            "LABELS": json.dumps([{"name": name} for name in labels]),
            "IS_ISSUE": "true" if is_issue else "false",
            "EVENT_ACTION": action,
            "CHANGED_LABEL": changed_label,
        }
        proc = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True
        )
        assert proc.returncode == 0, f"step failed: {proc.stdout}\n{proc.stderr}"

        mutations, option_ids = [], []
        if calls.exists():
            for line in calls.read_text().splitlines():
                argv = json.loads(line)
                blob = " ".join(argv)
                if "clearProjectV2ItemFieldValue" in blob:
                    mutations.append("clear")
                elif "updateProjectV2ItemFieldValue" in blob:
                    mutations.append("update")
                    option_ids += [
                        a.split("=", 1)[1] for a in argv if a.startswith("o=")
                    ]
            calls.unlink()
        return mutations, option_ids, proc.stdout

    return _run


# (case id, labels on the item, event action, label added/removed, expected mutations)
CASES = [
    # An issue that never had a priority label must not be touched, whatever
    # unrelated label activity happens on it. This is the reported bug.
    ("opened_no_labels", [], "opened", "", []),
    ("opened_only_other_labels", [DISCUSSION], "opened", "", []),
    ("unrelated_label_added", [AREA_CI], "labeled", AREA_CI, []),
    ("unrelated_label_added_beside_others", [DISCUSSION, AREA_CI], "labeled", AREA_CI, []),
    ("unrelated_label_removed", [AREA_CI], "unlabeled", DISCUSSION, []),
    # Removing the priority label is the one case that may clear the field.
    ("priority_label_removed", [DISCUSSION], "unlabeled", HIGH, ["clear"]),
    ("last_priority_label_removed_no_labels_left", [], "unlabeled", LOW, ["clear"]),
    # Setting still works, and wins over clearing when a priority label remains.
    ("priority_label_added", [HIGH], "labeled", HIGH, ["update"]),
    ("priority_label_present_on_open", [HIGH, DISCUSSION], "opened", "", ["update"]),
    ("one_of_two_priority_labels_removed", [LOW], "unlabeled", HIGH, ["update"]),
    ("unrelated_label_added_with_priority_present", [HIGH, AREA_CI], "labeled", AREA_CI, ["update"]),
    # Applying several labels at once fires several concurrent runs, each
    # carrying its own snapshot of the label set. A run whose snapshot has not
    # caught up with a priority label that was just added must not read that as
    # "no priority" and clear the field.
    ("stale_snapshot_on_priority_label_added", [], "labeled", HIGH, []),
    # Only the exact "priority: " prefix counts.
    ("lookalike_label_removed", [], "unlabeled", "prioritisation notes", []),
    ("prefix_without_separator_removed", [], "unlabeled", "priority:high", []),
    # ...and it has to be a prefix. A label that merely contains "priority: "
    # somewhere inside it is an ordinary label, so it neither sets the field
    # nor authorises a clear.
    ("priority_text_inside_phrase_added", [PHRASE], "labeled", PHRASE, []),
    ("hyphenated_priority_text_added", [HYPHENATED], "labeled", HYPHENATED, []),
    ("priority_text_inside_phrase_removed", [], "unlabeled", PHRASE, []),
]


@pytest.mark.parametrize(
    "case_id,labels,action,changed_label,expected",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_mutations_issued(run_step, case_id, labels, action, changed_label, expected):
    mutations, _, stdout = run_step(labels, action=action, changed_label=changed_label)
    assert mutations == expected, stdout


def test_priority_label_maps_to_matching_option(run_step):
    _, option_ids, stdout = run_step([HIGH], action="labeled", changed_label=HIGH)
    assert option_ids == ["opt-high"], stdout


def test_pull_requests_are_never_cleared(run_step):
    # clearProjectV2ItemFieldValue only supports issues.
    mutations, _, stdout = run_step(
        [], action="unlabeled", changed_label=HIGH, is_issue=False
    )
    assert mutations == [], stdout


def test_board_without_options_issues_no_update(run_step):
    # A Priority field with no single-select options cannot be set; the step
    # must skip rather than send a mutation with an empty option id.
    mutations, _, stdout = run_step(
        [HIGH], action="labeled", changed_label=HIGH, options=[]
    )
    assert mutations == [], stdout


def test_unknown_priority_value_issues_no_mutation(run_step):
    mutations, _, stdout = run_step(
        ["priority: bogus"], action="labeled", changed_label="priority: bogus"
    )
    assert mutations == [], stdout


def test_missing_event_context_does_not_clear(run_step, tmp_path):
    # If the event carries no label context at all the step must take the safe
    # branch, not abort under `set -u` and not clear.
    mutations, _, stdout = run_step([], action="", changed_label="")
    assert mutations == [], stdout


def test_step_env_wires_the_event_context():
    # The shell logic above is only correct if the workflow actually passes the
    # event action and the changed label into the step.
    env = _step_env()
    assert "github.event.action" in str(env.get("EVENT_ACTION", ""))
    assert "github.event.label.name" in str(env.get("CHANGED_LABEL", ""))
