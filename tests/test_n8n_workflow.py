"""Static checks on the n8n workflow file.

n8n is not run here, so these cannot show the workflow works. They catch the
mistakes that can be caught on paper: a connection to a node that does not
exist, or the workflow and the API disagreeing about the upload.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import main

WORKFLOW = Path(__file__).parent.parent / "n8n" / "workflows" / "invoice-intake.json"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def _node(workflow: dict, name: str) -> dict:
    return next(n for n in workflow["nodes"] if n["name"] == name)


def test_node_names_and_ids_are_unique(workflow):
    names = [n["name"] for n in workflow["nodes"]]
    ids = [n["id"] for n in workflow["nodes"]]
    assert len(names) == len(set(names))
    assert len(ids) == len(set(ids))


def test_every_connection_joins_two_real_nodes(workflow):
    names = {n["name"] for n in workflow["nodes"]}
    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for target in branch:
                assert target["node"] in names


def test_every_node_except_the_note_is_wired_in(workflow):
    wired = set(workflow["connections"])
    for outputs in workflow["connections"].values():
        wired |= {t["node"] for branch in outputs["main"] for t in branch}
    unwired = {n["name"] for n in workflow["nodes"]} - wired
    assert unwired == {"Sticky Note"}


def test_both_triggers_lead_to_the_agent(workflow):
    connections = workflow["connections"]
    assert connections["Webhook: document uploaded"]["main"][0][0]["node"] == (
        "Extract with InvoiceAgent"
    )
    assert connections["Read file from inbox"]["main"][0][0]["node"] == (
        "Extract with InvoiceAgent"
    )


def test_the_upload_matches_what_the_api_accepts(workflow):
    request = _node(workflow, "Extract with InvoiceAgent")["parameters"]
    assert request["method"] == "POST"
    assert request["url"].endswith("/process")
    assert request["contentType"] == "multipart-form-data"

    (field,) = request["bodyParameters"]["parameters"]
    # /process takes the document in a multipart field called "file".
    assert field["parameterType"] == "formBinaryData"
    assert field["name"] == "file"
    assert any(
        route.path == "/process" and "POST" in route.methods for route in main.app.routes
    )

    # Both triggers must hand over the document under the binary key the
    # request reads. The webhook names it after the form field; the file
    # reader is told the name.
    reader = _node(workflow, "Read file from inbox")["parameters"]
    assert reader["options"]["dataPropertyName"] == field["inputDataFieldName"] == "file"


def test_a_failed_extraction_does_not_stop_the_workflow(workflow):
    # The agent answers 422 when it cannot extract; that has to reach the
    # "needs review" branch instead of ending the execution with an error.
    response = _node(workflow, "Extract with InvoiceAgent")["parameters"]["options"]["response"]
    assert response["response"]["neverError"] is True
    assert response["response"]["fullResponse"] is True


def test_the_review_branch_and_the_processed_branch_both_notify(workflow):
    true_branch, false_branch = workflow["connections"]["Needs review?"]["main"]
    assert true_branch[0]["node"] == "Notify: needs review"
    assert false_branch[0]["node"] == "Notify: processed"
    for name in ("Notify: needs review", "Notify: processed"):
        node = _node(workflow, name)
        assert "$env.NOTIFY_WEBHOOK_URL" in node["parameters"]["url"]
        assert node["onError"] == "continueRegularOutput"


def test_the_workflow_ships_unpublished(workflow):
    assert workflow["active"] is False
