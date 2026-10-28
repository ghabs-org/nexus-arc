"""Isolated Linux, macOS and Windows speech subprocess ownership."""

import asyncio
import base64
import json
import subprocess
import sys


async def run(command, *, input=None, stdout=None, stderr=None, timeout=None, check=False):
    from nexus.core.studio_desktop_sidecar import worker_command

    module = (
        "nexus.core.studio_speech_windows"
        if sys.platform == "win32"
        else "nexus.core.studio_mcp_supervisor"
    )
    stopped = asyncio.Event()

    async def owned():
        process = await asyncio.create_subprocess_exec(
            *worker_command(module, "--speech", "--", *command),
            stdin=asyncio.subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
        )
        communication = None
        stop_wait = None
        try:
            if stopped.is_set():
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 3)
                return subprocess.CompletedProcess(command, process.returncode, b"", b"")
            payload = {"input": base64.b64encode(input).decode() if input is not None else None}
            process.stdin.write(json.dumps(payload).encode() + b"\n")
            await process.stdin.drain()

            async def collect():
                async def read(stream):
                    return await stream.read() if stream else None

                # communicate() closes stdin even with no input. Keep the owner
                # pipe open while independently draining both output streams.
                output, errors = await asyncio.gather(read(process.stdout), read(process.stderr))
                await process.wait()
                return output, errors

            communication = asyncio.create_task(collect())
            stop_wait = asyncio.create_task(stopped.wait())
            done, _ = await asyncio.wait(
                [communication, stop_wait],
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            timed_out = not done
            if communication not in done:
                process.stdin.close()
                try:
                    await asyncio.wait_for(asyncio.shield(communication), 3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                    raise RuntimeError(
                        "Local speech descendant cleanup remains unconfirmed"
                    ) from None
            output, errors = await communication
            if timed_out:
                raise subprocess.TimeoutExpired(command, timeout, output=output, stderr=errors)
            if check and process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command, output, errors)
            return subprocess.CompletedProcess(command, process.returncode, output, errors)
        finally:
            process.stdin.close()
            if stop_wait:
                stop_wait.cancel()
                await asyncio.gather(stop_wait, return_exceptions=True)
            if communication and not communication.done():
                communication.cancel()
                await asyncio.gather(communication, return_exceptions=True)
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    worker = asyncio.create_task(owned())
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
            stopped.set()
        except Exception:
            break
    if cancelled:
        worker.exception()
        raise asyncio.CancelledError
    return worker.result()
