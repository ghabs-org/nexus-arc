# PDF preview recovery

PDF preview failures now explain the failure category without recommending an
unrelated provider change. Existing successful preview fields and original-file
access remain unchanged. An unavailable extraction has empty text,
`extraction_available: false`, a safe message and an additive `error_code`.

| Code | Meaning |
| --- | --- |
| `pdf_timeout` | The worker exceeded the 15-second wall limit. |
| `pdf_dependency_missing` | An import required by the extraction worker is unavailable. |
| `pdf_unreadable` | The parser could not open the document, or it is encrypted. |
| `pdf_worker_unavailable` | The operating system could not launch the worker. |
| `pdf_worker_terminated` | The worker exited because of a signal. |
| `pdf_worker_failed` | Another nonzero worker exit occurred. |
| `pdf_invalid_response` | Worker output is not valid preview data. |

Messages exclude raw exception values, stderr, document contents and server paths.
Only dependency failures show installation guidance. No failure sends a file to
an external provider, changes its selected provider, or retries an external action.
The file remains available through its existing authorized artifact access.

The subprocess retains its existing 15-second wall timeout, 10-second CPU limit,
Linux memory limit and bounded input/extracted text. Source and frozen execution
use the same fixed worker protocol. Internal exit codes 20–22 describe dependency,
unreadable-document and other worker failures; successful JSON output is unchanged.
No persisted records need migration, and clients may ignore the additive field.

## PDF startup isolation — 26 October 2026

Investigation confirmed an unnecessary dependency: the old source `-m` worker
loaded Nexus package initializers, which imported AI-provider SDKs before the
extractor established its resource limits. A controlled broken Anthropic import
made a valid synthetic PDF fail with exit 1. The same document succeeded through
isolated-file execution, without reaching that provider. Import profiling also
measured seconds spent loading unrelated provider modules. This proves a startup
coupling defect; it does not establish the cause of the earlier unclassified
intermittent failure.

The framework now launches its fixed PDF script with Python `-I`, following the
existing isolated-file worker pattern. Frozen packaging includes the same trusted
script and dispatches the allowlisted PDF invocation before importing Studio.
Normal backend startup and non-PDF worker dispatch retain the existing entrypoint.
The JSON protocol, selected-page arguments, error categories and 15-second wall,
10-second CPU, Linux memory and input/text limits are unchanged. There is no
provider fallback, automatic retry, persisted-record migration or new endpoint.

The updated portable example verifies actual extracted text, immutable revision,
concurrent/repeated authenticated previews, out-of-range pages, damaged/encrypted
PDFs and parent EOF shutdown. For a deliberately built test backend:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_desktop_bundle.py \
  --executable /path/to/nexus-sidecar --profile core \
  --pdf-repetitions 8 --pdf-concurrency 4
```

A disposable macOS arm64 core backend passed this drill after relocation without
host Python on PATH. Eight authenticated preview requests at concurrency four
completed in 0.143–0.183 seconds each. Additional direct source/frozen repetitions
passed. Those timings describe these synthetic fixtures and this core build;
they are not a latency guarantee or a comparison with full-profile builds.

No app installer or DMG was built or replaced. Existing binaries remain older
code. Connected/full-profile packaging, installed native apps, other OSes and
personal VM acceptance for the new entrypoint remain separate. Exact source,
probe and runtime evidence is in `STUDIO-PDF-ISOLATION-CHECKPOINT.json`.

## Verification and limits

`tests/test_studio_pdf_failures.py` checks controlled timeout, launch, signal, exit,
malformed-output and concurrent mixed-document preview cases. Actual source workers
verify damaged/encrypted PDFs and a missing optional dependency without exposing
traceback details. The existing relocated desktop example now checks valid, damaged
and encrypted PDFs through the authenticated preview endpoint, alongside startup,
workspace writes and shutdown. See `STUDIO-PDF-PREVIEW-CHECKPOINT.json` for actual
build and run evidence.

## Load and concurrency reproduction — 27 October 2026

Controlled contention probes reproduced the exact historical failure signature (`pages`
omitted from `preview` with `extraction_available: false` and `error_code: "pdf_timeout"`)
without changing payload or timeout limits (16 MiB, 15s wall, 10s CPU):

- **Pre-isolation frozen binary under concurrent CPU load:** The installed pre-isolation
  macOS arm64 full-profile sidecar (`/Applications/Nexus Studio.app/Contents/Resources/sidecar/nexus-sidecar`,
  built before `790fbe3`) passed 3 relocated HTTP runs (16 repetitions at concurrency 4
  each = 48 authenticated previews) in 2.048–3.942s when uncontended, and 16/16 direct
  previews at concurrency 8 in 3.647–3.802s. Under 8-process CPU contention matching
  concurrent build activity, 16/16 direct workers at concurrency 8 and 8/8
  `StudioService.artifacts.preview` calls exceeded the 15-second wall timeout
  (`15.014–15.040s`), returning `extraction_available: false`, `error_code: "pdf_timeout"`,
  and omitting `pages` because every pre-isolation worker bootstrapped the full `nexus`
  and provider SDK package graph.
- **Isolated source and `FROZEN_ENTRYPOINT` under identical contention:** Under the same
  8-process CPU contention and concurrency 8, `StudioService.artifacts.preview` passed
  16/16 multi-page previews in `0.743–0.908s`, and `FROZEN_ENTRYPOINT` isolated dispatch
  passed 16/16 in `0.893–1.034s` (`32/32` at concurrency 8 in `0.253–0.507s` uncontended).

This reproduces and explains the 25 October 55.43-second connected-bundle timeout under
concurrent build load, and rules out `StudioService` lock contention or pipe leaks in
isolated dispatch. Keep installed native app acceptance open until the next explicit
release rebuild packages the isolated entrypoint. No resource limit was increased.
