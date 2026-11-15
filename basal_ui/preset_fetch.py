"""Fixed worker for downloading preset packs."""

import json
import sys

from .fetch import FetchError, fetch

try:
    result = {"result": fetch(json.load(sys.stdin), presets=True)}
except FetchError as exc:
    result = {"error": str(exc), "code": exc.code, "status": exc.status}
except Exception:
    result = {"error": "URL fetch worker failed.", "code": "fetch_failed", "status": 502}
json.dump(result, sys.stdout, ensure_ascii=True, allow_nan=False)
