import traceback

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()
_startup_error = None

try:
    from backend.main import app as backend_app

    app = backend_app
except Exception:
    _startup_error = traceback.format_exc()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def startup_error(path: str):
        return JSONResponse(
            {
                "error": "startup_import_failed",
                "path": path,
                "traceback": _startup_error,
            },
            status_code=500,
        )

