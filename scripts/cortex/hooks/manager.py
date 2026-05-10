#!/usr/bin/env python3
"""Runtime lifecycle hook dispatcher."""
import os
import subprocess
import sys


def dispatch(workspace, event_name, *args, **kwargs):
    """
    Run hooks/<event_name>.py from the workspace .agents directory when present.
    """
    hooks_dir = os.path.join(workspace, ".agents", "hooks")
    hook_script = os.path.join(hooks_dir, f"{event_name}.py")

    if not os.path.exists(hook_script):
        return None

    try:
        str_args = [str(a) for a in args]
        cmd = [sys.executable, hook_script] + str_args
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if res.returncode == 0:
            return res.stdout.strip()

        sys.stderr.write(f"[HOOK ERROR] {event_name}: {res.stderr.strip()}\n")
        return f"Error: {res.stderr.strip()}"

    except Exception as e:
        sys.stderr.write(f"[DISPATCH ERROR] {event_name}: {str(e)}\n")
        return f"Exception: {str(e)}"
