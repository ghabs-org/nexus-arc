"""Native parent lifetime over the existing private launch input pipe."""

import os
import threading


def watch_parent_input(stream, shutdown):
    """Stop on parent EOF or unexpected bytes after the launch credential.

    The shell retains the only writer. OS pipe closure also works after abnormal
    shell exit, without polling PIDs or risking termination of a reused process.
    The reader is process-scoped and daemonized; it never owns or closes stdin.
    Call shutdown from this separate thread, not the HTTP serving thread.
    """
    if not callable(getattr(stream, "read", None)):
        raise ValueError("Desktop parent input must be a readable stream")
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        descriptor = None

    def watch():
        try:
            # Avoid holding BufferedReader's lock during interpreter shutdown.
            # Native launch credentials use the same unbuffered input channel.
            if descriptor is None:
                stream.read(1)
            else:
                os.read(descriptor, 1)
        except (OSError, ValueError):
            pass  # Closed or broken parent channel also ends this launch.
        shutdown()

    thread = threading.Thread(target=watch, name="nexus-desktop-parent", daemon=True)
    thread.start()
    return thread
