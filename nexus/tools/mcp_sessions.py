"""Bounded stdio sessions, owned and closed by the same async task.

The bridge works across short-lived request loops. A failed call is never replayed.
Keys include account identity and a digest of resolved configuration/credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading


class MCPSessionPool:
    def __init__(self, *, max_sessions=16, idle_seconds=60, call_seconds=60):
        self.max_sessions = max_sessions
        self.idle_seconds = idle_seconds
        self.call_seconds = call_seconds
        self.entries = {}
        self.lock = threading.Lock()
        self.loop = None
        self.thread = None
        self.closed = False

    def start(self):
        with self.lock:
            if self.closed:
                raise RuntimeError("MCP session pool is closed")
            if self.thread:
                return
            self.loop = asyncio.new_event_loop()
            self.thread = threading.Thread(
                target=self.loop.run_forever, daemon=True, name="studio-mcp"
            )
            self.thread.start()

    async def invoke(
        self, identity, command, args, env, *, tool=None, arguments=None, authorize=None
    ):
        self.start()
        key = hashlib.sha256(
            json.dumps([identity, command, args, env], sort_keys=True).encode()
        ).hexdigest()
        future = asyncio.run_coroutine_threadsafe(
            self.submit(key, command, args, env, tool, arguments, authorize), self.loop
        )
        return await asyncio.wrap_future(future)

    async def submit(self, key, command, args, env, tool, arguments, authorize):
        if self.closed:
            raise RuntimeError("MCP session pool is closed")
        if key not in self.entries:
            if len(self.entries) >= self.max_sessions:
                raise ValueError("MCP session limit reached. Wait for idle sessions to close")
            queue = asyncio.Queue(maxsize=32)
            task = asyncio.create_task(self.serve(key, command, args, env, queue))
            self.entries[key] = (queue, task)
        queue, _ = self.entries[key]
        result = asyncio.get_running_loop().create_future()
        try:
            queue.put_nowait((tool, arguments, result, authorize))
        except asyncio.QueueFull:
            raise ValueError("MCP session is busy. Wait for pending calls to finish") from None
        return await result

    async def serve(self, key, command, args, env, queue):
        from nexus.tools.mcp import _require_mcp

        current = None
        error = RuntimeError("MCP session ended. Review the action outcome before retrying")
        try:
            session_type, parameters_type, stdio_client = _require_mcp()
            async with stdio_client(
                parameters_type(command=command, args=list(args or []), env=env)
            ) as (read, write):
                async with session_type(read, write) as session:
                    await asyncio.wait_for(session.initialize(), self.call_seconds)
                    while True:
                        try:
                            current = await asyncio.wait_for(queue.get(), self.idle_seconds)
                        except TimeoutError:
                            break
                        if current is None:
                            break
                        tool, arguments, future, authorize = current
                        if future.cancelled():
                            current = None
                            continue
                        if authorize:
                            authorize()
                        result = await asyncio.wait_for(
                            session.call_tool(tool, dict(arguments or {}))
                            if tool
                            else session.list_tools(),
                            self.call_seconds,
                        )
                        if tool:
                            if getattr(result, "isError", getattr(result, "is_error", False)):
                                raise RuntimeError("MCP server reported a tool error")
                            value = "\n".join(
                                getattr(block, "text", "")
                                for block in (result.content or [])
                                if getattr(block, "type", "") == "text"
                            )
                        else:
                            value = [
                                {
                                    "name": item.name,
                                    "description": item.description or "",
                                    "inputSchema": getattr(
                                        item, "inputSchema", getattr(item, "input_schema", {})
                                    ),
                                }
                                for item in (result.tools or [])
                            ]
                        if not future.done():
                            future.set_result(value)
                        current = None
        except BaseException:
            # SDK exception groups may contain server output or environment details.
            if current and not current[2].done():
                current[2].set_exception(error)
        finally:
            self.entries.pop(key, None)
            while not queue.empty():
                pending = queue.get_nowait()
                if pending and not pending[2].done():
                    pending[2].set_exception(error)

    async def shutdown(self):
        tasks = [entry[1] for entry in self.entries.values()]
        for queue, _ in list(self.entries.values()):
            # Cancellation closes contexts in their owning tasks, even with a full queue.
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if not self.thread:
                return
        asyncio.run_coroutine_threadsafe(self.shutdown(), self.loop).result(timeout=15)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()
