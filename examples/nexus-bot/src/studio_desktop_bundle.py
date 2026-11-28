"""Exercise a relocated frozen backend without a checkout or host Python on PATH."""

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import requests
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from nexus.core.studio_desktop import DesktopLaunchCredential


def exercise(executable, profile="core", pdf_repetitions=4, pdf_concurrency=2):
    if type(pdf_repetitions) is not int or not 1 <= pdf_repetitions <= 16:
        raise ValueError("Choose 1–16 PDF repetitions")
    if type(pdf_concurrency) is not int or not 1 <= pdf_concurrency <= 4:
        raise ValueError("Choose 1–4 concurrent PDF previews")
    executable = Path(executable).resolve()
    with tempfile.TemporaryDirectory(prefix="nexus-portable-acceptance-") as directory:
        root = Path(directory)
        runtime = root / "Relocated runtime"
        # Copy the whole one-folder bundle; rename only its executable.
        # Match native resource copying: file aliases become regular files.
        shutil.copytree(executable.parent, runtime, symlinks=False)
        binary = runtime / ("nexus backend.exe" if os.name == "nt" else "nexus backend")
        (runtime / executable.name).rename(binary)
        home = root / "home"
        home.mkdir()
        scratch = root / "temporary"
        scratch.mkdir()
        environment = {
            "PATH": "",
            "HOME": str(home),
            "TMPDIR": str(scratch),
            "TEMP": str(scratch),
            "TMP": str(scratch),
        }
        if os.name == "nt":
            environment["SystemRoot"] = os.environ["SystemRoot"]
            environment["USERPROFILE"] = str(home)
        info = subprocess.run(
            [str(binary), "--nexus-bundle-info"],
            cwd=runtime,
            env=environment,
            capture_output=True,
            check=True,
            timeout=30,
        )
        info = json.loads(info.stdout)
        assert (
            info["frozen"] and info["frontend_present"] and info["optional_sdk_presence"]["pypdf"]
        ), info
        checked = subprocess.run(
            [str(binary), "--nexus-bundle-check", profile],
            cwd=runtime,
            env=environment,
            capture_output=True,
            check=True,
            timeout=60,
        )
        checked = json.loads(checked.stdout)
        assert checked["profile"] == profile and checked["passed"], checked
        workspace = root / "workspace"
        log = root / "backend.log"
        credential = DesktopLaunchCredential.generate()
        child = None
        with log.open("w") as output, requests.Session() as client:
            client.trust_env = False
            try:
                child = subprocess.Popen(
                    [str(binary), "--dir", str(workspace), "--port", "0", "--desktop-launch-stdin"],
                    cwd=runtime,
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=output,
                )
                child.stdin.write(credential.line())
                child.stdin.flush()
                deadline = time.monotonic() + 20
                address = None
                while time.monotonic() < deadline:
                    assert child.poll() is None, log.read_text()[-3000:]
                    lines = log.read_text().splitlines()
                    address = next(
                        (
                            s.removeprefix("Nexus Studio: ")
                            for s in lines
                            if s.startswith("Nexus Studio: http://127.0.0.1:")
                        ),
                        None,
                    )
                    if address:
                        try:
                            response = client.get(
                                address + "/api/desktop/handshake",
                                headers={"X-Nexus-Desktop-Challenge": "ab" * 32},
                                timeout=1,
                                allow_redirects=False,
                            )
                            port = urlsplit(address).port
                            if response.status_code == 200 and response.json().get(
                                "proof"
                            ) == credential.proof("ab" * 32, str(workspace.resolve()), port):
                                break
                        except (requests.RequestException, ValueError):
                            pass
                    time.sleep(0.05)
                else:
                    raise AssertionError(
                        "Bundled authenticated startup timed out: " + log.read_text()[-3000:]
                    )
                assert client.get(address + "/api/bootstrap", timeout=3).status_code == 401
                client.headers["Cookie"] = credential.COOKIE + "=" + credential.session()
                bootstrap = client.get(address + "/api/bootstrap", timeout=5)
                bootstrap.raise_for_status()
                client.headers["X-Nexus-Studio-Token"] = bootstrap.json()["token"]

                def request(path, body=None):
                    response = client.request(
                        "POST" if body is not None else "GET",
                        address + path,
                        json=body,
                        timeout=20,
                        allow_redirects=False,
                    )
                    response.raise_for_status()
                    return response

                assert b"Nexus Studio" in request("/").content
                assert request("/favicon.ico").content.startswith(b"\x00\x00\x01\x00")
                assert request("/vendor/purify.js").content
                request(
                    "/api/agents",
                    {"name": "Portable reviewer", "instructions": "Review the supplied evidence."},
                )
                assert (workspace / "agents" / "portable-reviewer.yaml").is_file()
                writer = PdfWriter()
                page = writer.add_blank_page(width=612, height=792)
                font = DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    }
                )
                page[NameObject("/Resources")] = DictionaryObject(
                    {
                        NameObject("/Font"): DictionaryObject(
                            {NameObject("/F1"): writer._add_object(font)}
                        ),
                    }
                )
                stream = DecodedStreamObject()
                stream.set_data(b"BT /F1 12 Tf 72 720 Td (Portable document evidence) Tj ET")
                page[NameObject("/Contents")] = writer._add_object(stream)
                pdf = io.BytesIO()
                writer.write(pdf)
                artifact = request(
                    "/api/catalog/artifacts",
                    {
                        "name": "Portable PDF",
                        "data": {
                            "filename": "evidence.pdf",
                            "content_base64": base64.b64encode(pdf.getvalue()).decode(),
                        },
                    },
                ).json()
                preview = request("/api/catalog/artifacts/" + artifact["id"] + "/preview").json()
                assert preview.get("kind") == "pdf" and preview.get("pages") == 1, preview
                assert (
                    preview["extraction_available"]
                    and preview["text"] == "Portable document evidence"
                ), preview

                def repeat_preview(index):
                    started = time.monotonic()
                    # Each concurrent request owns its HTTP session; no mutable
                    # request/cookie state is shared between test threads.
                    with requests.Session() as reader:
                        reader.trust_env = False
                        response = reader.get(
                            address + "/api/catalog/artifacts/" + artifact["id"] + "/preview",
                            headers=dict(client.headers),
                            timeout=20,
                            allow_redirects=False,
                        )
                        response.raise_for_status()
                        result = response.json()
                        assert (
                            result.get("extraction_available")
                            and result.get("text") == "Portable document evidence"
                        ), result
                        assert result["pages"] == 1 and result["revision"] == artifact["revision"]
                    return round(time.monotonic() - started, 3)

                with ThreadPoolExecutor(max_workers=pdf_concurrency) as requests_pool:
                    preview_seconds = list(
                        requests_pool.map(repeat_preview, range(pdf_repetitions))
                    )
                outside = client.get(
                    address + "/api/catalog/artifacts/" + artifact["id"] + "/preview?page=2",
                    timeout=20,
                    allow_redirects=False,
                )
                assert outside.status_code == 400 and "outside" in outside.json()["error"]
                encrypted = io.BytesIO()
                writer.encrypt("example-only-password")
                writer.write(encrypted)
                for label, content in [
                    ("damaged", b"%PDF- invalid"),
                    ("encrypted", encrypted.getvalue()),
                ]:
                    item = request(
                        "/api/catalog/artifacts",
                        {
                            "name": label,
                            "data": {
                                "filename": label + ".pdf",
                                "content_base64": base64.b64encode(content).decode(),
                            },
                        },
                    ).json()
                    rejected = request("/api/catalog/artifacts/" + item["id"] + "/preview").json()
                    assert not rejected["extraction_available"], rejected
                    assert rejected["error_code"] == "pdf_unreadable", rejected
                    assert rejected["text"] == "" and "pages" not in rejected, rejected
                child.stdin.close()
                assert child.wait(15) == 0
                assert credential.line().decode().strip() not in log.read_text()
                return {
                    "relocated": True,
                    "host_python_on_path": False,
                    "authenticated_startup": True,
                    "workspace_write": True,
                    "frontend_and_favicon": True,
                    "frozen_pdf_worker": True,
                    "frozen_pdf_errors": True,
                    "pdf_preview_repetitions": pdf_repetitions,
                    "pdf_preview_concurrency": pdf_concurrency,
                    "pdf_preview_seconds": preview_seconds,
                    "frozen_pdf_text_and_page_bounds": True,
                    "parent_eof_shutdown": True,
                    "live_providers_used": False,
                    "optional_sdk_presence": info["optional_sdk_presence"],
                    "dependency_profile": checked,
                    "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                }
            finally:
                if child:
                    if child.stdin and not child.stdin.closed:
                        child.stdin.close()
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(5)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--profile", choices=["core", "connected", "full"], default="core")
    parser.add_argument("--pdf-repetitions", type=int, default=4)
    parser.add_argument("--pdf-concurrency", type=int, default=2)
    args = parser.parse_args()
    print(
        json.dumps(
            exercise(args.executable, args.profile, args.pdf_repetitions, args.pdf_concurrency),
            indent=2,
        )
    )
