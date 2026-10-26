"""`python -m sherpa_ui.openapi_export <path>` writes the app's OpenAPI JSON, from which the frontend's client is
generated. Building the app opens no connection, so the settings here are placeholders."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import SecretStr

from sherpa_ui.app import create_app
from sherpa_ui.settings import UiSettings

USAGE = "usage: python -m sherpa_ui.openapi_export <path>"
PLACEHOLDER = "unused"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write(f"{USAGE}\n")
        return 2

    settings = UiSettings(
        _env_file=None,  # type: ignore[call-arg]
        db_host=PLACEHOLDER,
        db_port=0,
        db_name=PLACEHOLDER,
        ui_user=PLACEHOLDER,
        ui_password=SecretStr(PLACEHOLDER),
    )
    schema = create_app(settings, static_dir=None).openapi()
    path = Path(argv[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
