"""Console entrypoint for Cortex runtime control."""
from __future__ import annotations

from cortex.runtime.control import main as control_main
from cortex.runtime.environment import require_virtualenv


def main(argv: list[str] | None = None) -> int:
    require_virtualenv()
    return control_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
