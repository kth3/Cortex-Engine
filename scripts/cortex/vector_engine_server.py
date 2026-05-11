from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 경로 설정 및 모듈 임포트
CORTEX_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = str(CORTEX_DIR.parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from cortex.runtime.engine_server import run_engine_server
from cortex.runtime.engine_worker import run_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help="Run as PyTorch Worker process")
    args, _ = parser.parse_known_args()

    if args.worker:
        run_worker()
    else:
        run_engine_server(Path(__file__).resolve())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
