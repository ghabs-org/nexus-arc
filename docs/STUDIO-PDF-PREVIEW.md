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
