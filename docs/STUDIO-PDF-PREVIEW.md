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

`tests/test_studio_pdf_failures.py` checks controlled timeout, launch, signal, exit
and malformed-output failures. Actual source workers verify damaged/encrypted PDFs
and a missing optional dependency without exposing traceback details. The existing
relocated desktop example now checks valid, damaged and encrypted PDFs through the
authenticated preview endpoint, alongside startup, workspace writes and shutdown.
See `STUDIO-PDF-PREVIEW-CHECKPOINT.json` for actual build and run evidence.

A prior connected-bundle repeat lost PDF extraction metadata. Its original cause
was not captured. These changes make a future failure identifiable; subsequent
passing runs do not establish that the historical intermittent cause is fixed.
Keep desktop stability acceptance open until any recurrence is diagnosed. No
resource limit was increased to turn that failure into a pass.
