#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path


RACKPATCH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACKPATCH_ROOT / "app"))

from common import stack_catalog  # noqa: E402


def main() -> int:
    payload = {"stacks": stack_catalog.load_stack_catalog()}
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
