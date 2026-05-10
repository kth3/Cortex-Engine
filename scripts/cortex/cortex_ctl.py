from __future__ import annotations

from runtime.control import main
from runtime.environment import require_virtualenv


require_virtualenv()


if __name__ == "__main__":
    raise SystemExit(main())
