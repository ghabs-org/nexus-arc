"""Bounded agent-case workers under the existing evaluation job and lease."""

import asyncio
from contextvars import ContextVar

current_evaluation_slot = ContextVar("studio_evaluation_slot", default=None)


async def execute_parallel(studio, run_id, payload, report, progress, execute_case, record_entry):
    target = payload["suite_snapshot"]["data"]
    repeats = payload["_experiment_settings"]["repeats"]
    workers = payload["_experiment_settings"]["workers"]
    if target["target_kind"] != "agent":
        raise ValueError("Parallel experiments require an agent target")
    completed = {
        ((entry["variant_index"] - 1) * len(target["cases"]) + entry["case"] - 1) * repeats
        + entry["repetition"]
        - 1
        for entry in report["results"]
    }
    dispatched = report.setdefault("dispatched_slots", [])
    if set(dispatched) - completed or any(entry.get("error") for entry in report["results"]):
        report["evaluation_status"] = "interrupted" if set(dispatched) - completed else "error"
        studio._write(studio._path("evaluation-reports", run_id), report)
        raise RuntimeError(
            "Experiment has interrupted or failed dispatched slots; automatic replay is disabled. Inspect the partial report before deliberately starting a new run."
        )
    studio._write(studio._path("evaluation-progress", run_id), progress)
    pending = iter(
        (ordinal, variant_index, index, repetition, case)
        for ordinal, (variant_index, index, repetition, case) in enumerate(
            (variant_index, index, repetition, case)
            for variant_index in range(len(payload["variants"]))
            for index, case in enumerate(target["cases"])
            for repetition in range(repeats)
        )
        if ordinal not in completed
    )
    stopped = False

    async def worker():
        nonlocal stopped
        while not stopped:
            if studio.closing or studio.run(run_id).get("cancel_requested"):
                raise asyncio.CancelledError
            item = next(pending, None)
            if item is None:
                return
            ordinal, variant_index, index, repetition, case = item
            checkpoint = f"{run_id}-{variant_index}-{index}" + (
                f"-repeat-{repetition}" if repeats > 1 else ""
            )
            # A receipt precedes any provider/tool effect. A missing result is uncertain,
            # never permission to repeat an already-dispatched invocation after restart.
            dispatched.append(ordinal)
            studio._write(studio._path("evaluation-reports", run_id), report)
            token = current_evaluation_slot.set(checkpoint)
            try:
                entry = await execute_case(variant_index, index, repetition, case, checkpoint)
                record_entry(entry, checkpoint)
                if entry.get("error"):
                    stopped = True
            except asyncio.CancelledError:
                studio.execution.record(
                    run_id, "evaluation-case", node_id=checkpoint, status="interrupted"
                )
                raise
            finally:
                current_evaluation_slot.reset(token)

    tasks = [asyncio.create_task(worker()) for _ in range(workers)]
    try:
        await asyncio.gather(*tasks)
    finally:
        # Owned tasks must not outlive the parent job's authorization or lease.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if stopped:
        raise RuntimeError(
            "Evaluation stopped after provider/runtime error; inspect the partial report"
        )
