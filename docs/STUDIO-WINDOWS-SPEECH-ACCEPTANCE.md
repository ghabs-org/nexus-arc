# Windows speech ownership acceptance

The framework's local speech owner passed native source acceptance on Windows
Server 2022 x64 with Python 3.11.9 and 3.13.17. Each job passed 37 checks without
skips plus all seven lifecycle cases. The tested source commit is
`5de733c831dbbd177699017d8826b032c2ab1c9b`;
[CI results](https://github.com/ghabs-org/nexus-arc/actions/runs/40746477508) and
[retained reports](STUDIO-WINDOWS-SPEECH-CHECKPOINT.json) record the exact scope.
Raw Windows hashes match that commit after exact LF-to-CRLF checkout conversion.
Actual speech models and installed-app acceptance remain pending. macOS mock tests verify
failure guards and orchestration; actual macOS service tests also exercise queued
and running cancellation and shutdown. Neither verifies Windows process behavior.
This gate does not build or install an application, download speech models, use a
microphone, call providers, access the personal VM, or create a DMG.

## Automated gate

`.github/workflows/studio-windows-speech.yml` runs on relevant pull requests or
manual workflow dispatch. Pushes to `codex/windows-speech-*` test branches also
run this gate without a pull request or merge. It uses Windows Server 2022 x64
and Python 3.11/3.13.
Each matrix job checks the portable guards, then runs the native consumer. A failed
portable gate prevents promotion through a successful native result. Evidence is
retained for 14 days, including results from failed jobs where files exist.

The workflow uses read-only repository permissions and does not persist checkout
credentials. It installs only the framework and test dependencies. It never
publishes packages or builds a desktop installer. Adding this workflow does not
mean it has executed: a Windows job must actually run against the source revision.

## Run on an available Windows x64 machine

Use an isolated Python environment with the framework installed. From the
repository root, run:

```powershell
python -m pip install --editable . pytest pytest-asyncio
python -m pytest -o addopts='' tests/test_studio_speech_windows.py tests/test_studio_speech_worker.py tests/test_studio_windows_acceptance_runner.py tests/test_studio_speech_service_lifecycle.py --junitxml=acceptance-results/portable.xml -q
python tests/studio_windows_speech_acceptance.py --report acceptance-results/native-windows.json
```

The native runner calls the existing example consumer. It checks native Win32
structure sizes, cancellation and timeout of detached descendants, abrupt owner
death, finite input bytes, stderr and nonzero command status. It also queues real
Studio transcription jobs, checks running cancellation and service shutdown, and
cancels a queued recording while the real executor is occupied. All cases require
terminal run state, no surviving owned child, no temporary recording or owner
record, and no generated artifact. Only the model command is replaced with a
synthetic process; service dispatch, input ownership and cleanup run unchanged.
Processes use only synthetic scripts in a temporary directory. Native checks have a 90-second outer
bound; the CI job has a 10-minute limit.

Exit status is 0 for a complete native pass, 1 for failure, and 2 for an unavailable
host. Another operating system never produces a skip or passing native record.
The report records platform, Python version, source hashes and the checkout
revision when GitHub supplies one. The only successful native record has both
`status: passed` and `native_verified: true`, with every expected case confirmed.
Keep the report with its portable JUnit result and compare its hashes to the
source revision being reviewed before updating acceptance status.

## Completion boundary

A hosted Windows Server result proves source process ownership and synthetic
Studio queue/cancellation/shutdown flows on that runner. It does not certify
Windows desktop installation, authentication, workspace flows, real Whisper/Piper
models, shutdown with those real models, signing or updates.
Those remain separate checks; desktop milestone 9 stays last. No account/device/VM
acceptance record is promoted automatically from this gate.
