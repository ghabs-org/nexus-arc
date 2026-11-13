"""Disposable sharing dialog acceptance server with synthetic files only.

Run: PYTHONPATH=. python tests/studio_sharing_acceptance.py
Uses production dialog functions, styles and HTTP routes in a temporary workspace.
This focused harness does not substitute for full-app recipient browser acceptance.
"""

import base64
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-sharing-preview-") as root:
        studio = StudioService(root)
        studio._write(
            studio._path("sessions", "sharing-demo"),
            {"id": "sharing-demo", "title": "Sharing acceptance", "messages": []},
        )
        for index in range(12):
            studio.catalog.save(
                "artifacts",
                {
                    "id": f"file-{index}",
                    "name": f"Synthetic file {index + 1}",
                    "data": {
                        "filename": ("Very-long-filename-" * 5 if index == 0 else "Report-")
                        + f"{index + 1}.txt",
                        "content_base64": base64.b64encode(b"Synthetic private evidence").decode(),
                    },
                },
            )
        source = Path("nexus/studio_static/features.js").read_text()
        fields = source[
            source.index("function fieldsDialog(") : source.index("function downloadJson(")
        ]
        sharing = source[
            source.index("async function shareConversation(") : source.index(
                'document.addEventListener("click",async event=>{const b='
            )
        ]
        fixture = (
            """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/studio.css"></head><body class="shared-page"><main><h1>Conversation sharing</h1><button id="open">Share snapshot</button><div id="result"></div></main><script>
        const state={sessionId:'sharing-demo'};
        const escapeHtml=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Nexus-Studio-Token':'synthetic-sharing'},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw new Error(data.error||r.status);return data;}
        const featureApi=(path,body)=>api('/api/studio/'+path,body);
        const showDetail=(title,html)=>{document.getElementById('result').innerHTML='<h2>'+escapeHtml(title)+'</h2>'+html;};
        """
            + fields
            + sharing
            + """
        document.getElementById('open').onclick=()=>shareConversation().catch(error=>{document.getElementById('result').textContent=error.message;});
        </script></body></html>"""
        )
        script = fixture.split("<script>", 1)[1].split("</script>", 1)[0]
        fixture = (
            fixture.split("<script>", 1)[0] + '<script src="/acceptance.js"></script></body></html>'
        )

        class Handler(make_studio_handler(studio, "synthetic-sharing")):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler protocol method
                if self.path == "/acceptance":
                    return self.send_bytes(200, fixture.encode(), "text/html; charset=utf-8")
                if self.path == "/acceptance.js":
                    return self.send_bytes(200, script.encode(), "text/javascript; charset=utf-8")
                return super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        print(f"http://127.0.0.1:{server.server_port}/acceptance", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            studio.close()


if __name__ == "__main__":
    main()
