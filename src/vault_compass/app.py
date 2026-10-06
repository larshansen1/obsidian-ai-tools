"""FastAPI app for Vault Compass.

Local-only daemon (127.0.0.1), started with: compass serve
"""

from fastapi import FastAPI
from pydantic import BaseModel


class StatusResponse(BaseModel):
    # Only the running flag: no paths or config, same rule as kai (ADR 0002).
    running: bool = True


def create_app() -> FastAPI:
    """Build the Compass app. Used by uvicorn as a factory."""
    app = FastAPI(title="Vault Compass")

    @app.get("/status")
    def status() -> StatusResponse:
        return StatusResponse()

    return app
