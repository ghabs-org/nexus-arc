"""Artifact-backed data tools; all user programs execute in the Docker sandbox."""

import asyncio
import base64
import contextvars
import hashlib
import json
import uuid

from nexus.core.studio_connections import execution_identity
from nexus.core.studio_sandbox import StudioSandbox

current_run_id = contextvars.ContextVar("studio_tool_run_id", default=None)

ACTION_PROFILES = {
    "query_sql": "data",
    "create_database": "data",
    "transform_jq": "data",
    "chart": "data",
    "python": "python",
    "imagemagick": "media",
    "detect_objects": "vision",
}

DATA_PROGRAM = r"""
import csv,io,itertools,json,os,pathlib,sqlite3,subprocess
params=json.loads(PARAMETERS)
root=pathlib.Path(os.environ['INPUT_DIR']);out=pathlib.Path(os.environ['OUTPUT_DIR'])
source=root/params['filename'];action=params['action']
if action in ('query_sql','create_database'):
    if source.suffix.lower() in ('.sqlite','.db'):
        db=sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True)
    else:
        db=sqlite3.connect(str(out/'dataset.sqlite') if action=='create_database' else ':memory:')
        if source.suffix.lower()=='.csv':
            reader=csv.reader(source.open());columns=next(reader);rows=list(itertools.islice(reader,100001))
        else:
            records=json.loads(source.read_text())
            if not isinstance(records,list) or not records or not all(isinstance(r,dict) for r in records):raise ValueError('Expected JSON array of objects')
            columns=list(records[0]);rows=[[r.get(c) for c in columns] for r in records]
        if not 1<=len(columns)<=100 or len(rows)>100000:raise ValueError('Dataset exceeds limits')
        quoted=[chr(34)+c.replace(chr(34),chr(34)*2)+chr(34) for c in columns]
        db.execute('CREATE TABLE data ('+','.join(quoted)+')')
        db.executemany('INSERT INTO data VALUES ('+','.join('?' for _ in columns)+')',rows);db.commit()
    if action=='query_sql':
        allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION}
        db.set_authorizer(lambda operation,*args:sqlite3.SQLITE_OK if operation in allowed else sqlite3.SQLITE_DENY)
        result=db.execute(params['query']);columns=[c[0] for c in result.description];rows=result.fetchmany(10001)
        if len(rows)>10000:raise ValueError('Query result exceeds 10000 rows')
        (out/'result.json').write_text(json.dumps({'columns':columns,'rows':rows},default=str))
        print(json.dumps({'columns':columns,'rows':rows[:20],'truncated_preview':len(rows)>20},default=str))
    else:print('Created dataset.sqlite with table data')
    db.close()
elif action=='transform_jq':
    if source.suffix.lower()=='.csv':value=list(csv.DictReader(source.open()))
    elif source.suffix.lower() in ('.yaml','.yml'):
        import yaml
        value=yaml.safe_load(source.read_text())
    else:value=json.loads(source.read_text())
    result=subprocess.run(['jq','-c',params['expression']],input=json.dumps(value).encode(),capture_output=True,check=True,timeout=15)
    (out/'result.json').write_bytes(result.stdout);print(result.stdout[:4096].decode(errors='replace'))
elif action=='chart':
    import plotly.graph_objects as go
    config=json.loads(source.read_text());figure=go.Figure(config)
    figure.write_image(str(out/'chart.png'),width=1200,height=800)
    print('Chart generated')
"""

IMAGE_PROGRAM = r"""
import json,os,pathlib,shutil,subprocess
params=json.loads(PARAMETERS)
root=pathlib.Path(os.environ['INPUT_DIR']);out=pathlib.Path(os.environ['OUTPUT_DIR'])
source=root/params['filename']
if params['action']=='imagemagick':
    command=[shutil.which('magick') or shutil.which('convert') or 'magick',str(source)]
    if params.get('width'):command+=['-resize',str(params['width'])+'x'+str(params['height'])+'>']
    if params.get('rotate'):command+=['-rotate',str(params['rotate'])]
    if params.get('grayscale'):command+=['-colorspace','Gray']
    command.append(str(out/'processed.png'));subprocess.run(command,check=True,timeout=30)
    print('Image processed')
elif params['action']=='detect_objects':
    pathlib.Path('/work/yolo').mkdir(exist_ok=True)
    os.environ['YOLO_CONFIG_DIR']='/work/yolo'
    os.environ['YOLO_AUTOINSTALL']='false'
    from ultralytics import YOLO
    import torch
    torch.set_num_threads(1)
    model=YOLO('/models/model.pt')
    result=model(str(source),device='cpu',verbose=False,max_det=100)[0]
    boxes=[{'label':result.names[int(box.cls[0])],'confidence':float(box.conf[0]),'xyxy':box.xyxy[0].tolist()} for box in result.boxes]
    (out/'detections.json').write_text(json.dumps(boxes));result.save(filename=str(out/'annotated.jpg'));print(json.dumps(boxes))
"""


class StudioDataTools:
    def __init__(self, studio, profiles=None):
        self.studio = studio
        self.sandbox = StudioSandbox(profiles)
        self.names = {}
        namespace = hashlib.sha256(str(studio.state).encode()).hexdigest()[:12]
        from nexus.tools import tool_registry

        definitions = {
            "query_sql": "Query a CSV/JSON/SQLite artifact using read-only SQL; produces a private result artifact",
            "create_database": "Convert CSV/JSON into a private SQLite artifact with table data",
            "transform_jq": "Transform a JSON/YAML/CSV artifact using JQ in an isolated worker",
            "chart": "Create a static chart from a Plotly JSON artifact in an isolated worker",
            "python": "Run reviewed/generated Python in a pinned, networkless container with explicitly selected artifact inputs",
            "imagemagick": "Resize, rotate or grayscale an explicitly selected image in an isolated worker",
            "detect_objects": "Detect objects using an installed YOLO model in a networkless worker",
        }
        for action, description in definitions.items():
            name = f"studio:{namespace}:{action}"

            def bind(selected):
                async def invoke(**kwargs):
                    return await self.execute(selected, kwargs)

                return invoke

            function = bind(action)
            tool_registry.register(
                name, function, description=description, category="Data and media"
            )
            self.names[name] = function

    def close(self):
        from nexus.tools import tool_registry

        for name, function in self.names.items():
            tool_registry.unregister(name, func=function)

    def inventory(self):
        return {
            "items": [
                {
                    "id": name,
                    "configured": ACTION_PROFILES[name.rsplit(":", 1)[1]] in self.sandbox.profiles,
                    "profile": ACTION_PROFILES[name.rsplit(":", 1)[1]],
                    "billing": "Local container compute",
                    "setup": "Configure an installed, pinned local analysis worker. Host execution and automatic image downloads are disabled.",
                }
                for name in self.names
            ]
        }

    def actor(self):
        if not self.studio.team:
            return None
        identity = execution_identity.get()
        self.studio.team.assert_execution(identity)
        return {"nexus_id": identity, "role": self.studio.team.bindings[identity]}

    async def execute(self, action, arguments):
        actor = self.actor()
        identities = arguments.get("artifact_ids") or (
            [arguments["artifact_id"]] if arguments.get("artifact_id") else []
        )
        if not isinstance(identities, list) or len(identities) > 5:
            raise ValueError("Choose up to five input artifacts")
        files = []
        for identity in identities:
            record = self.studio.collaboration.owned(
                self.studio.catalog.get("artifacts", identity), actor
            )
            files.append(
                {
                    "filename": record["data"]["filename"],
                    "content_base64": base64.b64encode(self.studio.artifacts.get(record)).decode(),
                }
            )
        if len({f["filename"] for f in files}) != len(files):
            raise ValueError("Input files must have distinct filenames")
        if action != "python" and len(files) != 1:
            raise ValueError("Choose exactly one artifact for this tool")
        if action == "create_database" and not files[0]["filename"].lower().endswith(
            (".csv", ".json")
        ):
            raise ValueError("Database creation requires a CSV or JSON artifact")
        parameters = {"action": action, "filename": files[0]["filename"] if files else ""}
        if action == "python":
            code, profile = arguments.get("code"), "python"
        else:
            if action == "query_sql":
                query = arguments.get("query")
                if not isinstance(query, str) or not 1 <= len(query) <= 16000:
                    raise ValueError("Supply a SQL query up to 16000 characters")
                parameters["query"] = query
            if action == "transform_jq":
                expression = arguments.get("expression")
                if not isinstance(expression, str) or not 1 <= len(expression) <= 4000:
                    raise ValueError("Supply a JQ expression up to 4000 characters")
                parameters["expression"] = expression
            if action == "imagemagick":
                for key, default, maximum in [
                    ("width", 1000, 4096),
                    ("height", 1000, 4096),
                    ("rotate", 0, 360),
                ]:
                    value = arguments.get(key, default)
                    if type(value) is not int or not 0 <= value <= maximum:
                        raise ValueError(f"Invalid image {key}")
                    parameters[key] = value
                parameters["grayscale"] = arguments.get("grayscale") is True
            program = IMAGE_PROGRAM if action in {"imagemagick", "detect_objects"} else DATA_PROGRAM
            code = program.replace("PARAMETERS", repr(json.dumps(parameters)))
            profile = ACTION_PROFILES[action]
        result = await asyncio.to_thread(self.sandbox.execute, code, files, profile)
        # Container work may finish after a cancellation or access revocation.
        # Recheck ownership before publishing any result into the workspace.
        with self.studio.lock:
            self.actor()
            run_id = current_run_id.get()
            if run_id:
                run = self.studio.collaboration.owned(self.studio.run(run_id), actor)
                if run.get("cancel_requested") or run.get("status") not in {"queued", "running"}:
                    raise asyncio.CancelledError
            for identity in identities:
                self.studio.collaboration.owned(
                    self.studio.catalog.get("artifacts", identity), actor
                )
            return self.publish(result, actor, run_id)

    def publish(self, result, actor, run_id):
        artifacts = []
        for file in result["files"]:
            saved = self.studio.catalog.save(
                "artifacts",
                {
                    "id": uuid.uuid4().hex,
                    "name": file["filename"],
                    "description": file.get("description", ""),
                    "requester_nexus_id": actor["nexus_id"] if actor else None,
                    "data": {
                        "filename": file["filename"],
                        "content_base64": file["content_base64"],
                    },
                },
            )
            if run_id:
                saved["generated_by_run"] = run_id
                self.studio._write(self.studio.catalog._path("artifacts", saved["id"]), saved)
                run = self.studio.run(run_id)
                parts = list(run.get("structured_content", []))
                if len(parts) < 40:
                    parts.append(
                        {
                            "type": "artifact",
                            "artifact_id": saved["id"],
                            "revision": saved["revision"],
                            "filename": file["filename"],
                        }
                    )
                self.studio._update_run(run_id, content_version=1, structured_content=parts)
                self.studio.execution.record(
                    run_id,
                    "artifact",
                    status="generated",
                    artifact_id=saved["id"],
                    artifact_revision=saved["revision"],
                )
            artifacts.append(
                {"id": saved["id"], "filename": file["filename"], "revision": saved["revision"]}
            )
        return {"output": result["stdout"], "artifacts": artifacts}
