from __future__ import annotations

import sys


def _check_venv() -> None:
    """가상 환경(venv) 내부에서 실행 중인지 확인하여 시스템 파이썬 오용 방지"""
    in_venv = hasattr(sys, "real_prefix") or (sys.base_prefix != sys.prefix)
    if not in_venv:
        print("\n[ERROR] Cortex must be run within the virtual environment.")
        print("💡 Hint: Use 'uv run python scripts/cortex/cortex_ctl.py' or activate .venv first.\n")
        sys.exit(1)


_check_venv()

from runtime.control import main


if __name__ == "__main__":
    raise SystemExit(main())
