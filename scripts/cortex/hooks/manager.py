#!/usr/bin/env python3
"""Runtime lifecycle hook dispatcher."""
import subprocess
import sys
from pathlib import Path


def dispatch(workspace, event_name, *args, **kwargs):
    """
    Run hooks/<event_name>.py from the workspace .cortex directory when present.
    """
    hook_script = Path(workspace) / ".cortex" / "hooks" / f"{event_name}.py"

    if not hook_script.exists():
        return None

    try:
        str_args = [str(a) for a in args]
        cmd = [sys.executable, str(hook_script), *str_args]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if res.returncode == 0:
            return res.stdout.strip()

        sys.stderr.write(f"[HOOK ERROR] {event_name}: {res.stderr.strip()}\n")
        return f"Error: {res.stderr.strip()}"

    except Exception as e:
        sys.stderr.write(f"[DISPATCH ERROR] {event_name}: {str(e)}\n")
        return f"Exception: {str(e)}"
