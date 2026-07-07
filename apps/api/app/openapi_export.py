"""Dump the API's OpenAPI spec to stdout (used by scripts/gen-client.sh, offline).

    uv run python -m app.openapi_export > apps/web/openapi.json
"""

from __future__ import annotations

import json

from app.main import app


def main() -> None:
    print(json.dumps(app.openapi(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
