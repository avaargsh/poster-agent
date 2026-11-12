"""Contracts for explicit CI lanes and the revisions they validate."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEAD = "${{ github.event.pull_request.head.sha }}"


def workflow(name):
    # Preserve GitHub's `on` key as text instead of YAML 1.1's boolean True.
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_skipped_label_jobs_cannot_cancel_other_validation_lanes():
    ci = workflow("ci.yml")
    compat = workflow("langgraph-compat.yml")
    assert "concurrency" not in ci
    assert "concurrency" not in compat
    groups = []
    for job in [*ci["jobs"].values(), *compat["jobs"].values()]:
        policy = job["concurrency"]
        assert policy["cancel-in-progress"] == "true"
        groups.append(policy["group"])
    assert len(set(groups)) == len(groups)


def test_browser_and_compatibility_labels_check_out_the_declared_source_head():
    for filename, job in [("ci.yml", "browser-smoke"), ("langgraph-compat.yml", "migration")]:
        steps = workflow(filename)["jobs"][job]["steps"]
        checkout = next(
            step for step in steps if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout.get("with", {}).get("ref") == SOURCE_HEAD


def test_frontend_changes_receive_merge_and_source_build_checks():
    ci = workflow("ci.yml")
    assert "frontend/**" in ci["on"]["pull_request"]["paths"]
    assert set(ci["on"]) == {"workflow_dispatch", "pull_request"}
    assert set(ci["on"]["pull_request"]["types"]) == {"ready_for_review", "labeled"}
    steps = ci["jobs"]["frontend-check"]["steps"]
    checkout_positions = [
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(checkout_positions) == 2
    first, second = checkout_positions
    assert "ref" not in steps[first].get("with", {})
    assert steps[second]["with"]["ref"] == SOURCE_HEAD
    for start, end in [(first, second), (second, len(steps))]:
        commands = "\n".join(step.get("run", "") for step in steps[start:end])
        assert "npm ci" in commands
        assert "npm run check" in commands
