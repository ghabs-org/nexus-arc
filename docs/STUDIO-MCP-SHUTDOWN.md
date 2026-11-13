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

## Linux detached descendants

The source runtime now gives each Linux stdio session a separate supervisor when
procfs and Python pidfds are available. One-shot requests and pooled sessions use
the same launch policy. The supervisor enables the Linux child-subreaper role
before starting the server. Orphaned descendants, including a double-forked daemon
that changed session/process group, are adopted by that dedicated owner.

The supervisor forwards raw stdin with bounded buffering. It observes owner EOF
even when the child stops reading and the input pipe fills. Server exit, SIGTERM,
SIGINT or owner EOF start a one-second cleanup pass. The pass reads only its own
kernel child list, opens stable PID descriptors, checks the parent relationship,
kills owned children and reaps them as their remaining descendants are adopted.
It never scans unrelated processes or trusts a persisted PID. An unrelated
sentinel remains alive in the actual acceptance cases. An exhausted cleanup
budget reports uncertainty on stderr rather than claiming success.

The mechanism uses the documented Linux
[subreaper role](https://www.man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)
and [PID-descriptor signalling](https://www.man7.org/linux/man-pages/man2/pidfd_send_signal.2.html).
It is cleanup ownership, not an execution sandbox. Direct SIGKILL of the
supervisor, hostile executables, privilege/namespace changes and host root require
a stronger service cgroup/container boundary. Those cases remain outside this
acceptance. A SIGKILLed Studio owner is covered: its separate supervisor remains
alive long enough to observe EOF and clean up.

No connector configuration, environment-selection policy, authorization, stored
record or automatic retry changes. Other platforms retain SDK cleanup. Linux
without the required kernel/procfs support logs a warning and retains that legacy
policy. Once supervised startup is selected, initialization failure never retries
the server without supervision. Source execution uses an isolated stdlib file;
trusted frozen-worker dispatch and data inclusion are prepared, but no frozen
Linux app was built or verified and existing DMGs were not rebuilt.

After updating the source package, the example checks Linux detached cleanup:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_mcp_lifecycle.py --detached
PYTHONPATH=. python examples/nexus-bot/src/studio_mcp_lifecycle.py --detached --persistent
```

The example refuses that mode before launch on unsupported platforms. Its
ordinary POSIX process-group mode remains available on macOS. Upgrade by stopping
active MCP work, updating the package and restarting Studio normally. No state
migration is needed; rollback returns to SDK group cleanup and its old detached
child limitation. See `STUDIO-LINUX-MCP-CHECKPOINT.json` for scoped evidence.
