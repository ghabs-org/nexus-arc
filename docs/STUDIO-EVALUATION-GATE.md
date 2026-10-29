# Offline evaluation release gate

Studio already enforces saved experiment thresholds during execution. The offline
gate lets an operator check that evidence in a local script or CI job without
restarting Studio, invoking providers, or applying a release.

## Export and check

1. Open Evaluations or Experiment Lab, then open a report.
2. Select **Download report JSON**. The download contains inputs, answers and
   grading details; store it privately.
3. Check the downloaded file:

```sh
nexus eval check-report evaluation-report.json
nexus eval check-report evaluation-report.json --threshold 0.9
```

The optional threshold can strengthen the report's saved threshold but cannot
lower it. Every variant must meet the effective threshold independently. A strong
variant cannot hide a weak variant behind the overall average.

Exit codes:

- `0`: complete evidence and every variant passes.
- `1`: threshold failure or blocked evidence, including missing results,
  nonterminal execution, provider errors or judge errors.
- `2`: invalid arguments, unreadable JSON or an unsupported/malformed report.

The command prints a versioned JSON decision containing counts and per-variant
outcomes. It does not echo task inputs, answers or provider error text. Variant
names remain visible; treat the summary as private if those names are sensitive.

## Evidence rules

The gate recomputes outcomes from boolean checks and unique
variant/case/repetition slots. It ignores cached aggregate counts and acceptance
claims. All expected slots must be present, and the report must explicitly mark
execution complete with a terminal evaluation status. Errors block acceptance,
even at a zero threshold; missing measurements are not successful evaluations.

The supported report is the current native Studio shape, with `variants`,
`runs_per_example`, `expected_total`, `pass_threshold`, `complete`,
`evaluation_status` and `results`. Files are bounded to eight MiB. Duplicate JSON
fields and inconsistent case outcomes are rejected. Existing workspace records
and routes do not change; no migration is needed.

This is a consistency and threshold check, not a signature verification or proof
of report provenance. It does not prove model quality beyond the chosen cases,
compare two runs for regression, or deploy/roll back anything. Keep CI reports
in trusted private storage and separately pin the tested resource revision.

## Framework consumer

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_evaluation_gate.py evaluation-report.json
```

This thin consumer uses the same framework validator as the CLI. Validation uses
actual locally generated Studio reports with a synthetic provider, malformed and
incomplete reports, CLI exit codes, and browser export behavior. It does not call
a paid provider or contact a personal VM.
