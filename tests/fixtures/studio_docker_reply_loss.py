"""Real Unix-transport faults in front of an existing filtered Docker socket.

This fixture never invents a daemon result. It forwards a mutation, receives the
actual successful response, then closes the client before acknowledging it.
Subsequent requests are disconnected until the test explicitly restores access.
No raw Docker socket, public listener, credential or task payload log is needed.
"""

import asyncio
import http.client
import io
import json
import os
import re
import threading
from urllib.parse import urlsplit


class _ResponseSocket:
    def __init__(self, data):
        self.data = data

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.data)


class ReplyLossProxy:
    def __init__(self, path, upstream):
        self.path, self.upstream = path, upstream
        self.mode = "lose-create"
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.errors = []
        self.created_id = None
        self.counts = {
            "create_requests": 0,
            "create_forwarded": 0,
            "delete_requests": 0,
            "delete_forwarded": 0,
            "start_requests": 0,
            "lost_create_replies": 0,
            "lost_delete_replies": 0,
            "offline_connections": 0,
        }
        self.loop = self.stop = self.thread = None
        self.handlers = set()

    def set_mode(self, mode):
        if mode not in {"lose-delete", "passthrough"}:
            raise ValueError("Choose a supported recovery phase")
        with self.lock:
            self.mode = mode

    def snapshot(self):
        with self.lock:
            return {**self.counts, "unexpected_errors": list(self.errors)}

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        upstream_writer = None
        try:
            async with asyncio.timeout(15):
                head = await reader.readuntil(b"\r\n\r\n")
                method, target, _ = head.split(b"\r\n", 1)[0].decode("ascii").split()
                path = re.sub(r"^/v\d+(?:\.\d+)?", "", urlsplit(target).path)
                creating = method == "POST" and path == "/containers/create"
                deleting = method == "DELETE" and path.startswith("/containers/")
                starting = method == "POST" and path.endswith("/start")
                with self.lock:
                    if creating:
                        self.counts["create_requests"] += 1
                    if deleting:
                        self.counts["delete_requests"] += 1
                    if starting:
                        self.counts["start_requests"] += 1
                    mode = self.mode
                    if mode == "offline":
                        self.counts["offline_connections"] += 1
                        return
                    # An unexpected retry must not introduce another side effect.
                    if starting or (creating and self.counts["create_forwarded"]):
                        return
                    if deleting and mode not in {"lose-delete", "passthrough"}:
                        return
                    if creating:
                        self.counts["create_forwarded"] += 1
                    if deleting:
                        self.counts["delete_forwarded"] += 1
                headers = {}
                for line in head.split(b"\r\n")[1:]:
                    if b":" in line:
                        key, value = line.split(b":", 1)
                        headers[key.lower()] = value.strip()
                if b"transfer-encoding" in headers:
                    raise ValueError("Chunked client requests are outside this fixed fixture")
                length = int(headers.get(b"content-length", b"0"))
                if not 0 <= length <= 1024 * 1024:
                    raise ValueError("Fixture request exceeds its bound")
                body = await reader.readexactly(length)
                upstream_reader, upstream_writer = await asyncio.open_unix_connection(self.upstream)
                upstream_writer.write(head + body)
                await upstream_writer.drain()
                response = bytearray()
                while chunk := await upstream_reader.read(65536):
                    response.extend(chunk)
                    if len(response) > 1024 * 1024:
                        raise ValueError("Fixture response exceeds its bound")
                parsed = http.client.HTTPResponse(_ResponseSocket(bytes(response)))
                parsed.begin()
                if creating and mode == "lose-create":
                    if parsed.status != 201:
                        raise ValueError("Create was not committed by the actual daemon")
                    identity = json.loads(parsed.read())["Id"]
                    if not re.fullmatch(r"[a-f0-9]{64}", identity):
                        raise ValueError("Actual daemon returned an invalid identity")
                    with self.lock:
                        self.created_id = identity
                        self.counts["lost_create_replies"] += 1
                        self.mode = "offline"
                    return
                if deleting and mode == "lose-delete":
                    if parsed.status != 204:
                        raise ValueError("Delete was not committed by the actual daemon")
                    with self.lock:
                        self.counts["lost_delete_replies"] += 1
                        self.mode = "offline"
                    return
                writer.write(response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            # The real client may abandon a negotiation connection after a fault.
            pass
        except Exception as error:
            with self.lock:
                self.errors.append(type(error).__name__)
        finally:
            if upstream_writer:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self.handlers.discard(task)

    def __enter__(self):
        def run():
            async def serve():
                self.loop = asyncio.get_running_loop()
                self.stop = asyncio.Event()
                server = await asyncio.start_unix_server(self._handle, str(self.path))
                os.chmod(self.path, 0o600)
                self.ready.set()
                async with server:
                    await self.stop.wait()
                for handler in list(self.handlers):
                    handler.cancel()
                if self.handlers:
                    await asyncio.gather(*list(self.handlers), return_exceptions=True)

            try:
                asyncio.run(serve())
            except Exception as error:
                with self.lock:
                    self.errors.append(type(error).__name__)
                self.ready.set()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        if not self.ready.wait(10) or self.errors:
            raise RuntimeError("Reply-loss fixture did not start")
        return self

    def __exit__(self, *args):
        self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError("Reply-loss fixture did not stop")
        self.path.unlink(missing_ok=True)
