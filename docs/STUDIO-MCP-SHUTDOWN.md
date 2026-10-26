# MCP shutdown ownership

Studio now tracks direct connector requests in the same execution ownership map
used by runs. Requests made inside an already owned run, including inherited
child tasks, reuse that ownership. Closing Studio rejects new connector dispatch,
cancels each owned task once and waits for direct request contexts to settle.

Persistent MCP sessions start their SDK teardown immediately. Previously the pool
waited five seconds before cancelling an active call. One-shot SDK contexts and
persistent contexts now close concurrently before final process-group cleanup.
The existing native twelve-second grace and backend ten-second watchdog remain.
The direct-context settlement budget is eight seconds, including shutdown work;
no unbounded request wait or automatic action retry is introduced.

The reproduced source failure was specific: Studio.close returned in 0.001 seconds
while a one-shot server and its child survived. Persistent close took 9.023 seconds.
After the fix, both modes stop a SIGTERM-resistant server and its child in about
four seconds. The SDK owns their separate process groups; Studio's backend group
kill alone could not reach them.

## Use and acceptance

`examples/nexus-bot/src/studio_mcp_lifecycle.py` consumes the framework directly.
`studio_mcp_desktop_cleanup.py` drives an actual frozen backend through its
protected connector API, then requests stop or kills its synthetic launcher.
Both use an independent local JSON-RPC stdio server with a resistant child and
an append-only call log. No external account or live remote mutation is involved.

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_mcp_lifecycle.py
PYTHONPATH=. python examples/nexus-bot/src/studio_mcp_lifecycle.py --persistent
NEXUS_ACCEPTANCE_SIDECAR=/path/to/nexus-sidecar PYTHONPATH=. python -m pytest tests/studio_mcp_desktop_acceptance.py -q -o addopts=''
```

Process-group acceptance is explicitly POSIX-only and separate from required
platform-neutral ownership regressions. Windows job-object/process-tree behavior
still requires native verification. Async embedders closing active requests should
call `await asyncio.to_thread(studio.close)` so their request loop can perform
cleanup; the normal server closes from its control thread.

No endpoint, stored connector record, account permission, selected-tool grant or
workflow definition changes. Shutdown cannot undo a remote effect that already
happened. Review uncertain remote outcomes before retrying manually. This gate
covers ordinary descendants in the SDK's owned group. Daemonized descendants that
leave it and Docker containers outside host process groups remain separate work.

See `STUDIO-MCP-SHUTDOWN-CHECKPOINT.json` for exact test and Beta 2 image evidence.
