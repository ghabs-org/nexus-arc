"""Re-running one step of a finished YAML workflow run as a new run."""

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_team import wait


@pytest.fixture
def studio(tmp_path):
    prompts = []

    class Recorder:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            return AgentResult(success=True, output=f"result {len(prompts)}")

    service = StudioService(tmp_path, provider_factory=lambda name: Recorder())
    service.save_definition("agents", {"id": "a", "name": "A", "instructions": "x"})
    service.save_definition(
        "workflows",
        {
            "id": "w",
            "name": "W",
            "steps": [
                {"id": f"s{n}", "agent_type": "a", "prompt_template": f"STEP{n}"} for n in (1, 2, 3)
            ],
        },
    )
    source = wait(
        service, service.start_run({"kind": "workflow", "workflow_id": "w", "task": "go"})
    )
    assert source["status"] == "completed" and len(prompts) == 3
    prompts.clear()
    yield service, source, prompts
    service.close()


def outputs(service, run_id):
    return service._read(service._path("yaml-runs", run_id))["outputs"]


def test_continue_reruns_chosen_and_later_steps_with_note(studio):
    service, source, prompts = studio
    plan = service.step_rerun.describe(source["id"])
    assert plan["can_rerun"] and [s["id"] for s in plan["steps"]] == ["step-1", "step-2", "step-3"]
    queued = service.step_rerun.queue(
        source["id"], {"step_id": "step-2", "note": "Use the calculation tool"}
    )
    run = wait(service, queued)
    assert run["status"] == "completed" and run["rerun_of"]["step_name"] == "s2"
    assert len(prompts) == 2 and "STEP2" in prompts[0] and "STEP3" in prompts[1]
    assert "Additional input for this re-run:\nUse the calculation tool" in prompts[0]
    assert "result 1" in prompts[0]  # step 1's saved output reaches step 2
    assert "Additional input" not in prompts[1]
    assert outputs(service, run["id"])["step-1"] == outputs(service, source["id"])["step-1"]
    assert service.run(source["id"])["status"] == "completed"
    assert outputs(service, source["id"])["step-2"] == "result 2"


def test_only_mode_reuses_later_outputs(studio):
    service, source, prompts = studio
    run = wait(
        service, service.step_rerun.queue(source["id"], {"step_id": "step-2", "mode": "only"})
    )
    assert run["status"] == "completed" and len(prompts) == 1 and "STEP2" in prompts[0]
    assert outputs(service, run["id"])["step-3"] == outputs(service, source["id"])["step-3"]


def test_first_step_rerun_and_changed_workflow(studio):
    service, source, prompts = studio
    run = wait(service, service.step_rerun.queue(source["id"], {"step_id": "step-1"}))
    assert run["status"] == "completed" and len(prompts) == 3
    service.update_definition(
        "workflows",
        "w",
        {
            "name": "W",
            "revision": next(i for i in service.definitions("workflows") if i["id"] == "w")[
                "revision"
            ],
            "steps": [{"id": "x", "agent_type": "a", "prompt_template": "X"}],
        },
    )
    changed = wait(service, service.step_rerun.queue(source["id"], {"step_id": "step-2"}))
    assert changed["status"] == "failed" and "steps changed" in changed["error"]


def test_rejects_bad_input_and_client_seeded_outputs(studio):
    service, source, prompts = studio
    with pytest.raises(ValueError, match="Choose a step"):
        service.step_rerun.queue(source["id"], {"step_id": "step-9"})
    with pytest.raises(ValueError, match="up to"):
        service.step_rerun.queue(source["id"], {"step_id": "step-1", "note": "x" * 5000})
    with pytest.raises(ValueError, match="continue the workflow"):
        service.step_rerun.queue(source["id"], {"step_id": "step-1", "mode": "skip"})
    forged = {"source_run_id": source["id"], "step_num": 3, "mode": "only", "note": "", "names": []}
    run = wait(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": "w", "task": "go", "_step_rerun": forged}
        ),
    )
    assert run["status"] == "completed" and len(prompts) == 3  # every step really ran


def test_only_workflow_runs_can_rerun(studio):
    service, source, prompts = studio
    chat = wait(service, service.start_run({"kind": "chat", "task": "hi"}))
    plan = service.step_rerun.describe(chat["id"])
    assert not plan["can_rerun"] and "workflow runs" in plan["blocked_reason"]


def test_workflow_runs_record_output_step_names(studio):
    service, source, prompts = studio
    run = service.run(source["id"])
    assert run["output_steps"] == ["s1", "s2", "s3"]
    assert run["output"].startswith("s1\n")


def test_finished_run_shares_as_labelled_step_snapshot(studio):
    service, source, prompts = studio
    shared = service.collaboration.share({"run_id": source["id"]}, None)
    snapshot = service.collaboration.get_share(shared["id"])
    assert [(m.get("label"), m["content"]) for m in snapshot["messages"]] == [
        ("Task", "go"),
        ("1. s1", "result 1"),
        ("2. s2", "result 2"),
        ("3. s3", "result 3"),
    ]
    assert snapshot["title"] == "w" and "run_id" not in snapshot
    service._update_run(source["id"], status="running")
    with pytest.raises(ValueError, match="after it finishes"):
        service.collaboration.share({"run_id": source["id"]}, None)
