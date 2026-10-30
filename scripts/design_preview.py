"""Throwaway UI exploration: three layouts on /?variant=A|B|C, never the production entry point.

Run: uv run --no-sync python scripts/design_preview.py
Uses the real SPA and read-only APIs, an isolated temporary config, and no model subprocesses.
"""

import argparse
import os
import tempfile
from pathlib import Path

import uvicorn
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8879)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="basal-design-preview-") as temporary:
        os.environ["BASAL_UI_CONFIG"] = str(Path(temporary) / "settings.json")
        os.environ["HF_HUB_OFFLINE"] = "1"
        from basal_ui import settings
        from basal_ui.app import STATIC, create_app

        settings.set_check_updates(False)
        settings.set_auto_load(False)
        app = create_app()

        @app.middleware("http")
        async def preview_only(request, call_next):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                return JSONResponse(
                    {"detail": "Read-only design prototype: no settings, downloads or inference are changed."},
                    status_code=403,
                )
            return await call_next(request)

        def prototype(request: Request):
            variant = request.query_params.get("variant", "A")
            if variant not in {"A", "B", "C", "original"}:
                variant = "A"
            shell = (STATIC / "index.html").read_text()
            shell = shell.replace("<title>basal</title>", "<title>basal · design prototype</title>")
            shell = shell.replace("<body>", f'<body data-design="{variant}">')
            shell = shell.replace("</head>", '<link rel="stylesheet" href="/static/design-prototype.css"></head>')
            shell = shell.replace(
                "</body>",
                '<script src="/static/design-dashboard.js" defer></script>'
                '<script src="/static/design-prototype.js" defer></script></body>',
            )
            return HTMLResponse(shell)

        # Shadow only the page route; retain the app's Host, Origin, cache and framing middleware.
        app.router.routes.insert(0, APIRoute("/", prototype, methods=["GET", "HEAD"], include_in_schema=False))

        print(f"Design preview: http://127.0.0.1:{args.port}/?variant=A#dashboard", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
