from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cortex.runtime.control import main
from cortex.runtime.environment import require_virtualenv


require_virtualenv()


if __name__ == "__main__":
    raise SystemExit(main())
