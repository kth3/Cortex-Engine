"""Compatibility shim for the watch daemon package."""

from cortex.watch.daemon import *  # noqa: F401,F403

if __name__ == "__main__":
    main()
