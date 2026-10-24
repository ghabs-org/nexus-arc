# Isolated research and data workers

Studio reuses its existing agent tool loop for iterative research. Grant the
workspace-specific `:research` tool to an agent and set `max_tool_calls` explicitly.
Each research call accepts 1–4 queries, at most five results per query and 0–4
fetched pages. The next agent turn receives the results as untrusted data and can
refine the query or synthesize a cited answer. The loop enforces the total tool
budget; it never chooses another search provider after failure. The existing
Codex/Claude Code provider choice remains available for synthesis. Search API use
has its own explicitly selected credentials and billing.

Web citation references persist on the run and conversation, with source URL,
retrieval time, excerpt and content digest. This records evidence retrieved during
the run; it does not guarantee a remote page remains unchanged or trustworthy.
The search adapter remains explicitly configured in private `research.search`
settings. Existing web/image search, public page-fetch and source restrictions
remain unchanged.

### Search without a configured account

In shared (team) mode, when `research.search` is not configured, an agent granted
`:web_search` or `:research` that runs on a Codex, Claude Code or OpenCode
subscription worker uses the worker's built-in web search and page fetch instead.
This applies to chat and workflow agent steps. The agent's Studio search and
fetch tools are left out of its tool loop, its other tools still run there, and
the worker runs at the `web` tool level (search and fetch, no shell). Searches then
use the worker's subscription and are not recorded as Studio web citations. An
explicit `none` tool level in chat keeps the tools off.

Once `research.search` is configured, the Studio tools are used again everywhere,
with citations. Antigravity, API providers and desktop mode keep the Studio
tools, which then need the account. `:image_search` always needs Tavily or Brave,
and workflow `tool` nodes that call `:web_search` directly always need the account,
because no model runs in those nodes.

## Build selected workers

Builds install dependencies only when explicitly invoked. Catalog browsing and
normal tool execution never build images, install packages or pull a missing
image. Select a reviewed base image digest for the target machine, then build:

```sh
docker --context YOUR_CONTEXT build --build-arg BASE_IMAGE=python@sha256:YOUR_BASE_DIGEST \
  -t nexus-analysis:reviewed deploy/studio/analysis-worker
docker --context YOUR_CONTEXT image inspect nexus-analysis:reviewed --format '{{.Id}}'
```

Use the returned immutable `sha256:...` image ID in private runtime settings.
The analysis image includes JQ, ImageMagick, Chromium, fonts, PyYAML, Plotly and
Kaleido. Python package versions are pinned in its requirements file; base image
and operating-system package selection remain explicit operator build inputs.
The final image ID, rather than a mutable tag, identifies the installed worker.

For CPU object detection, use the separate optional image:

```sh
docker --context YOUR_CONTEXT build --build-arg BASE_IMAGE=python@sha256:YOUR_PYTHON_312_DIGEST \
  -f deploy/studio/analysis-worker/Dockerfile.vision \
  -t nexus-vision:reviewed deploy/studio/analysis-worker
docker --context YOUR_CONTEXT image inspect nexus-vision:reviewed --format '{{.Id}}'
```

This image pins PyTorch 2.9.1, torchvision 0.24.1 and Ultralytics 8.3.235. Supply
an already installed, trusted model file. Runtime mounts that file read-only at
`/models/model.pt`; no model is bundled or downloaded by Studio. Model files and
optional third-party packages retain their upstream licenses. See the
[Ultralytics YOLO11 documentation](https://docs.ultralytics.com/models/yolo11/)
for model formats, capabilities and licensing.

## Configure private runtime profiles

Add `analysis_workers` to the existing private runtime settings file. Replace
placeholders with installed image IDs, selected context and ordinary model path:

```json
{
  "analysis_workers": {
    "data": {
      "image": "sha256:YOUR_ANALYSIS_IMAGE_ID",
      "docker_context": "desktop-linux",
      "timeout": 60,
      "pids_limit": 256,
      "open_files": 256
    },
    "python": {
      "image": "sha256:YOUR_ANALYSIS_IMAGE_ID",
      "docker_context": "desktop-linux"
    },
    "media": {
      "image": "sha256:YOUR_ANALYSIS_IMAGE_ID",
      "docker_context": "desktop-linux"
    },
    "vision": {
      "image": "sha256:YOUR_VISION_IMAGE_ID",
      "docker_context": "desktop-linux",
      "model_path": "/absolute/path/to/reviewed-model.pt",
      "memory_mb": 2048,
      "timeout": 120,
      "pids_limit": 256,
      "open_files": 256
    }
  }
}
```

On a personal Linux VM, choose its installed Docker context instead. An explicit
context routes image inspection, execution and forced cleanup to that daemon,
regardless of ambient `DOCKER_HOST`. Omitting the new context field preserves
existing Docker environment behavior. Model files must be readable by the
container's non-root user; paths containing commas or symlinks are refused.
A remote daemon must have the selected model path on its own host.

Chromium chart export needs more file descriptors and processes than the original
64/64 defaults. The data profile above selects 256/256. Limits remain bounded:
16–512 processes, 64–1024 open files, 128–4096 MiB memory, 1–120 seconds and one CPU.
Existing profiles retain defaults of 64 processes/files, 768 MiB and 30 seconds.
ImageMagick and CPU vision run under the same restrictions. Vision uses one
PyTorch compute thread and temporary configuration under `/work`.

## Tools and outputs

Select registered tools through Tools, explicit agent grants or workflow nodes:

- `create_database`: CSV/JSON to SQLite, using table `data`.
- `query_sql`: read-only SQL on CSV/JSON/SQLite; up to 10,000 result rows.
- `transform_jq`: JQ expression on JSON/YAML/CSV.
- `chart`: Plotly figure JSON to a static 1200×800 PNG.
- `python`: generated/reviewed Python with explicitly selected artifact inputs.
- `imagemagick`: bounded resize, rotation and grayscale on a selected image.
- `detect_objects`: CPU detection with selected installed YOLO model; at most 100
  detections, JSON data and annotated JPEG output.

Each operation accepts `artifact_id`, or `artifact_ids` for Python. Non-Python
operations require exactly one input. CSV/JSON database conversion allows at most
100 columns and 100,000 rows. CSV loading stops at the first excess row. SQL uses
SQLite's authorizer to deny writes. JQ expressions and Python code remain bounded
by the existing request limits and worker timeout.

Input bytes travel through bounded standard input into the container's temporary
input directory. Output bytes return as private Nexus artifacts. The only host
bind mount is the explicitly selected vision model; no workspace or credential
folder is mounted. Containers run non-root, without a network, with a read-only
root filesystem, no capabilities, bounded temporary storage and no inherited
host environment. HTML, charts and model software cannot retrieve remote assets
from these workers.

Generated artifacts retain revision and run provenance, appear as attachment
links and execution events, and remain accessible through existing Nexus access
checks. Cancellation, revoked team access and lost input ownership prevent late
result publication. A stopped conversation does not synchronously terminate every
already running container; existing worker deadlines still apply. No host fallback
or automatic retry is attempted. Failure diagnostics retain only a bounded stderr
tail.

## Reproduce acceptance

The example builds a six-operation workflow in temporary state and validates actual
SQLite, SQL/JQ, PNG, ImageMagick and Python outputs:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_data_workers.py \
  --image sha256:YOUR_ANALYSIS_IMAGE_ID --docker-context YOUR_CONTEXT
```

Add `--vision-image sha256:YOUR_VISION_IMAGE_ID --model-path /installed/model.pt
--sample-image /selected/image.jpg` to exercise vision too. The example never
changes a running Studio instance, persists credentials or downloads input files.

The dedicated acceptance files intentionally fall outside the required unit-test
filename pattern. Explicit execution requires installed prerequisites and fails
if they are absent; there are no dependency skips:

```sh
export NEXUS_ACCEPTANCE_WORKER_IMAGE=sha256:YOUR_ANALYSIS_IMAGE_ID
export NEXUS_ACCEPTANCE_VISION_IMAGE=sha256:YOUR_VISION_IMAGE_ID
export NEXUS_ACCEPTANCE_DOCKER_CONTEXT=YOUR_CONTEXT
export NEXUS_ACCEPTANCE_VISION_MODEL=/installed/yolo11n.pt
export NEXUS_ACCEPTANCE_VISION_SAMPLE=/installed/upstream-bus.jpg
PYTHONPATH=. python -m pytest tests/studio_data_worker_acceptance.py \
  tests/studio_vision_worker_acceptance.py -q -o addopts=''
```

Vision acceptance specifically uses the upstream YOLO11n/bus fixture, asserting
bus/person labels and bounded coordinates; arbitrary images require their own
expected labels. The local acceptance record freezes fixture URLs and digests.
No account entitlement, production data quality or deployed-VM behavior is inferred
from these tests.

## Migration

The new profile fields are optional and additive. Existing private settings,
resource records and tool request/response fields remain readable. New optional
run artifact parts use the existing structured conversation format. No bulk
state rewrite, authentication change, scheduler or frontend build is required.
Without configured worker profiles, tools remain unavailable for execution.
The inventory's `configured` flag means a profile exists; runtime still verifies
installed images and prerequisites. Complete plugin readiness remains separate.
