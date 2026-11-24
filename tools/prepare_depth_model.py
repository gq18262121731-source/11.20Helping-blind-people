"""Download and verify the official Depth Anything V2 Small source and checkpoint."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.vision.model_setup import ensure_depth_assets


def main():
    result = ensure_depth_assets(settings.depth_code_path, settings.depth_model_path,
                                 progress=lambda step: print(step, flush=True))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
